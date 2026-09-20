import hashlib
import importlib.util
import io
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "hf_snapshot_download.py"
SPEC = importlib.util.spec_from_file_location("hf_snapshot_download", MODULE_PATH)
downloader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(downloader)
sys.modules["hf_snapshot_download"] = downloader

CHECK_SPEC = importlib.util.spec_from_file_location(
    "check_chinese_transcription", Path(__file__).parents[1] / "scripts" / "check_chinese_transcription.py"
)
checker = importlib.util.module_from_spec(CHECK_SPEC)
CHECK_SPEC.loader.exec_module(checker)


class _RangeHandler(BaseHTTPRequestHandler):
    payload = b""
    ranges: ClassVar[list] = []

    def do_GET(self):
        range_header = self.headers.get("Range")
        type(self).ranges.append(range_header)
        start = int(range_header.removeprefix("bytes=").removesuffix("-")) if range_header else 0
        body = type(self).payload[start:]
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Length", str(len(body)))
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{len(type(self).payload)-1}/{len(type(self).payload)}")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def range_server():
    _RangeHandler.payload = b"verified model bytes"
    _RangeHandler.ranges = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/model.bin", _RangeHandler
    finally:
        server.shutdown()
        thread.join()


def test_resumes_partial_download_and_atomically_publishes_verified_file(tmp_path, range_server):
    url, handler = range_server
    destination = tmp_path / "model.bin"
    partial = destination.with_suffix(".bin.part")
    partial.write_bytes(handler.payload[:8])

    downloader.download_verified(
        url,
        destination,
        expected_size=len(handler.payload),
        expected_sha256=hashlib.sha256(handler.payload).hexdigest(),
    )

    assert destination.read_bytes() == handler.payload
    assert not partial.exists()
    assert handler.ranges == ["bytes=8-"]


def test_complete_verified_partial_is_published_without_network(tmp_path, monkeypatch):
    payload = b"already complete and verified"
    destination = tmp_path / "model.bin"
    partial = destination.with_suffix(".bin.part")
    partial.write_bytes(payload)
    monkeypatch.setattr(
        downloader,
        "_open",
        lambda *args, **kwargs: pytest.fail("complete verified partial must not use network"),
    )

    downloader.download_verified(
        "https://example.invalid/model.bin",
        destination,
        expected_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_git_blob=hashlib.sha1(
            b"blob " + str(len(payload)).encode() + b"\0" + payload
        ).hexdigest(),
    )

    assert destination.read_bytes() == payload
    assert not partial.exists()


def test_complete_corrupt_partial_is_removed_and_downloaded_from_zero(
    tmp_path, range_server
):
    url, handler = range_server
    destination = tmp_path / "model.bin"
    partial = destination.with_suffix(".bin.part")
    partial.write_bytes(b"x" * len(handler.payload))

    downloader.download_verified(
        url,
        destination,
        expected_size=len(handler.payload),
        expected_sha256=hashlib.sha256(handler.payload).hexdigest(),
    )

    assert destination.read_bytes() == handler.payload
    assert not partial.exists()
    assert handler.ranges == [None]


def test_hash_mismatch_never_publishes_model(tmp_path, range_server):
    url, _ = range_server
    destination = tmp_path / "model.bin"

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        downloader.download_verified(
            url,
            destination,
            expected_size=len(_RangeHandler.payload),
            expected_sha256="0" * 64,
        )

    assert not destination.exists()
    assert not destination.with_suffix(".bin.part").exists()


def test_downloads_from_mirror_rejecting_default_library_user_agent(tmp_path, range_server, monkeypatch):
    url, handler = range_server
    original_get = handler.do_GET

    def mirror_get(self):
        if self.headers.get("User-Agent", "").startswith("Python-urllib/"):
            self.send_error(403, "Default library clients are not accepted")
            return
        original_get(self)

    monkeypatch.setattr(handler, "do_GET", mirror_get)
    destination = tmp_path / "model.bin"
    downloader.download_verified(
        url, destination, expected_size=len(handler.payload),
        expected_sha256=hashlib.sha256(handler.payload).hexdigest(),
    )
    assert destination.read_bytes() == handler.payload


class _Response(io.BytesIO):
    def __init__(self, body, *, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_response_overrun_is_never_published_or_kept_as_partial(tmp_path, monkeypatch):
    destination = tmp_path / "model.bin"
    monkeypatch.setattr(downloader, "_open", lambda request, timeout: _Response(b"123456"))

    with pytest.raises(ValueError, match="exceeds expected size"):
        downloader.download_verified("https://example.invalid/model", destination, expected_size=5)

    assert not destination.exists()
    assert not destination.with_suffix(".bin.part").exists()


def test_resume_rejects_wrong_content_range_start_before_writing(tmp_path, monkeypatch):
    destination = tmp_path / "model.bin"
    partial = destination.with_suffix(".bin.part")
    partial.write_bytes(b"123")
    response = _Response(
        b"456",
        status=206,
        headers={"Content-Range": "bytes 0-2/6"},
    )
    monkeypatch.setattr(downloader, "_open", lambda request, timeout: response)

    with pytest.raises(ValueError, match="Content-Range start"):
        downloader.download_verified("https://example.invalid/model", destination, expected_size=6)

    assert partial.read_bytes() == b"123"


def test_git_blob_hash_verifies_non_lfs_file(tmp_path):
    payload = b'{"model":"small"}\n'
    path = tmp_path / "config.json"
    path.write_bytes(payload)
    expected = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()

    downloader.verify_git_blob(path, expected)

    with pytest.raises(ValueError, match="Git blob mismatch"):
        downloader.verify_git_blob(path, "0" * 40)


def test_windows_registry_proxy_is_used_when_environment_only_has_no_proxy():
    assert downloader.select_proxies(
        {"no": "127.0.0.1"},
        {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
    ) == {
        "no": "127.0.0.1",
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }


@pytest.mark.parametrize(
    "text",
    [
        "项目预算为12800元，负责人是李明。团队每周五复盘。下周三前完成原始资料归档。",
        "項目預算為12800元，負責人是李明。團隊每週五復盤。下週三前完成原始資料歸檔。",
    ],
)
def test_acceptance_handles_observed_simplified_and_traditional_asr_variants(text):
    assert all(checker.evaluate_checks(text).values())


@pytest.mark.parametrize("error", ["归到", "歸道"])
def test_acceptance_does_not_treat_observed_action_errors_as_archive(error):
    checks = checker.evaluate_checks(
        f"预算12800元，负责人李明，每周五复盘，下周三前完成原始资料{error}。"
    )
    assert checks["archive_action"] is False
