"""Legacy Word process-boundary tests; real antiword acceptance runs separately on Ubuntu."""

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest
from backend.adapters import build_registry
from backend.config import AppConfig
from backend.storage.obsidian import ObsidianWriter

OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
EXTRACTED = "旧版文档验证：保留原始资料，负责人李明。\n项目 金额 截止日期\n资料采购 12800 2026-09-30\n待办：周五完成归档核对。"


def legacy_adapter(tmp_path, suffix=".doc"):
    config = AppConfig(data_dir=tmp_path / "data", vault_dir=tmp_path / "vault")
    path = tmp_path / f"预算 & 来源{suffix}"
    path.write_bytes(OLE_HEADER + b"converter-boundary fixture")
    return build_registry(config).for_file(path), path, config


def converter_process(monkeypatch, tmp_path, body):
    """Run a real bounded child process in place of the unavailable antiword binary."""
    shim = tmp_path / "converter.py"
    shim.write_text(body, encoding="utf-8")
    original_popen = subprocess.Popen

    def start(command, **kwargs):
        if command[0] != "antiword":
            return original_popen(command, **kwargs)
        assert isinstance(command, list)
        assert command[1:3] == ["-m", "UTF-8.txt"]
        assert kwargs.get("shell", False) is False
        assert Path(command[-1]).is_absolute()
        return original_popen([sys.executable, str(shim), *command[1:]], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", start)


@pytest.mark.parametrize("suffix", [".doc", ".DOC"])
def test_legacy_word_text_and_original_reach_portable_vault(tmp_path, monkeypatch, suffix):
    adapter, path, config = legacy_adapter(tmp_path, suffix)
    fixture = Path(__file__).parent / "fixtures" / "legacy.doc"
    original = fixture.read_bytes()
    assert original.startswith(OLE_HEADER)
    path.write_bytes(original)
    converter_process(monkeypatch, tmp_path,
                      f"import sys\nsys.stdout.buffer.write({EXTRACTED.encode('utf-8')!r})\n")

    fetched = asyncio.run(adapter.fetch(path))
    note = ObsidianWriter(config).write(adapter.normalize(fetched))
    text = note.read_text("utf-8")
    for expected in ("负责人李明", "资料采购", "12800", "2026-09-30", "周五完成归档核对"):
        assert expected in text
    assert next(config.vault_dir.rglob(f"*{suffix}")).read_bytes() == original
    assert fetched.metadata["extractor"] == "antiword"
    assert "file:///" not in text


def test_doc_missing_converter_has_actionable_error(tmp_path, monkeypatch):
    adapter, path, _ = legacy_adapter(tmp_path)
    original_popen = subprocess.Popen

    def missing(command, **kwargs):
        if command[0] == "antiword":
            raise FileNotFoundError("antiword unavailable")
        return original_popen(command, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", missing)
    with pytest.raises(RuntimeError, match="DOC.*antiword"):
        asyncio.run(adapter.fetch(path))


def test_doc_invalid_header_is_rejected_before_converter(tmp_path, monkeypatch):
    adapter, path, _ = legacy_adapter(tmp_path)
    path.write_bytes(b"not a binary Word document")
    converter_process(monkeypatch, tmp_path, "raise AssertionError('must not execute')\n")
    with pytest.raises(ValueError, match="DOC.*格式"):
        asyncio.run(adapter.fetch(path))


@pytest.mark.parametrize("case,body,expected", [
    ("corrupt", "import sys\nsys.stderr.write('not a Word document')\nsys.exit(1)\n", "DOC.*失败"),
    ("empty", "print('  ')\n", "DOC.*正文"),
    ("bad_encoding", "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe')\n", "DOC.*UTF-8"),
])
def test_doc_converter_failures_do_not_return_partial_content(
    tmp_path, monkeypatch, case, body, expected,
):
    adapter, path, _ = legacy_adapter(tmp_path)
    converter_process(monkeypatch, tmp_path, body)
    with pytest.raises(ValueError, match=expected):
        asyncio.run(adapter.fetch(path))


def test_doc_timeout_terminates_converter(tmp_path, monkeypatch):
    adapter, path, _ = legacy_adapter(tmp_path)
    monkeypatch.setattr(adapter, "TIMEOUT_SECONDS", 0.2)
    converter_process(monkeypatch, tmp_path, "import time\ntime.sleep(30)\n")
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="DOC.*超时"):
        asyncio.run(adapter.fetch(path))
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_doc_output_limit_stops_child_before_unbounded_output(tmp_path, monkeypatch, stream):
    adapter, path, _ = legacy_adapter(tmp_path)
    monkeypatch.setattr(adapter, "MAX_OUTPUT_BYTES", 64)
    monkeypatch.setattr(adapter, "MAX_ERROR_BYTES", 64)
    converter_process(monkeypatch, tmp_path,
                      f"import sys, time\nsys.{stream}.buffer.write(b'x' * 1024)\n"
                      f"sys.{stream}.flush()\ntime.sleep(30)\n")
    started = time.monotonic()
    with pytest.raises(ValueError, match="DOC.*输出.*上限"):
        asyncio.run(adapter.fetch(path))
    assert time.monotonic() - started < 5
