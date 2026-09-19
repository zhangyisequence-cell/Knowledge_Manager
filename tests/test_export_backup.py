"""Exercise the restricted exporter with real, independently restorable archives."""
import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import server_backup


def exporter():
    spec = importlib.util.spec_from_file_location("export_backup", SCRIPTS / "export_backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def archive(tmp_path):
    source = tmp_path / "source"
    (source / "vault").mkdir(parents=True)
    (source / "data").mkdir()
    (source / "vault" / "合成笔记.md").write_text("合成备份验收", encoding="utf-8")
    (source / "data" / "original.bin").write_bytes(bytes(range(256)) * 5000)
    return server_backup.create_backup(source, tmp_path / "backups")


def test_latest_and_get_export_real_restorable_bytes(archive, tmp_path):
    module = exporter()
    output = io.BytesIO()
    module.export_request("latest", archive.parent, output)
    manifest = json.loads(output.getvalue().decode("utf-8"))
    assert manifest == {"name": archive.name, "size": archive.stat().st_size,
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}
    output = io.BytesIO()
    module.export_request("get " + archive.name, archive.parent, output)
    assert output.getvalue() == archive.read_bytes()
    copied = tmp_path / "copied.tar.gz"
    copied.write_bytes(output.getvalue())
    restored = tmp_path / "restored"
    server_backup.restore_backup(copied, restored)
    assert (restored / "vault" / "合成笔记.md").read_text("utf-8") == "合成备份验收"
    assert (restored / "data" / "original.bin").read_bytes() == bytes(range(256)) * 5000


@pytest.mark.parametrize("command", [
    "", " latest", "latest\n", "latest ", "latest;id", "LATEST", "latest --root=/etc",
    "get ../shadow", "get /etc/shadow", "get knowledge-20260919T120000-deadbeef.tar.gz;id",
    "get  knowledge-20260919T120000-deadbeef.tar.gz", "get\tknowledge-20260919T120000-deadbeef.tar.gz",
    "get knowledge-20260919T120000-deadbeef.tar.gz\n", "FOO=bar latest",
    "get knowledge-20261399T120000-deadbeef.tar.gz", "get knowledge-20260919T120000-DEADBEEF.tar.gz",
])
def test_unsafe_request_rejected_before_output(archive, command):
    output = io.BytesIO()
    with pytest.raises(ValueError):
        exporter().export_request(command, archive.parent, output)
    assert output.getvalue() == b""


@pytest.mark.parametrize("checksum", [None, "0" * 64, "wrong-name"])
def test_missing_or_wrong_sidecar_never_exports(archive, checksum):
    sidecar = Path(str(archive) + ".sha256")
    if checksum is None:
        sidecar.unlink()
    elif checksum == "wrong-name":
        sidecar.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  other.tar.gz\n")
    else:
        sidecar.write_text(checksum + "  " + archive.name + "\n")
    for request in ("latest", "get " + archive.name):
        output = io.BytesIO()
        with pytest.raises(ValueError):
            exporter().export_request(request, archive.parent, output)
        assert output.getvalue() == b""


def test_archive_manifest_is_checked_even_with_matching_sidecar(archive):
    archive.write_bytes(b"not a real archive")
    Path(str(archive) + ".sha256").write_text(
        hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n"
    )
    with pytest.raises(ValueError):
        exporter().export_request("latest", archive.parent, io.BytesIO())


def test_false_internal_manifest_fails_even_with_valid_archive_checksum(archive):
    with tarfile.open(archive, "w:gz") as bundle:
        for name, content in [("vault/note.md", b"content"),
                              ("manifest.json", b'{"version":1,"files":{}}')]:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))
    Path(str(archive) + ".sha256").write_text(
        hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n"
    )
    with pytest.raises(ValueError, match="manifest"):
        exporter().export_request("latest", archive.parent, io.BytesIO())


def test_partial_is_ignored_and_valid_newest_name_wins_over_mtime(archive):
    newer = archive.parent / "knowledge-20990101T000000-deadbeef.tar.gz"
    newer.write_bytes(archive.read_bytes())
    Path(str(newer) + ".sha256").write_text(
        hashlib.sha256(newer.read_bytes()).hexdigest() + "  " + newer.name + "\n"
    )
    os.utime(newer, (1, 1))
    (archive.parent / "knowledge-20990102T000000-deadbeef.tar.partial").write_bytes(b"unfinished")
    output = io.BytesIO()
    exporter().export_request("latest", archive.parent, output)
    assert json.loads(output.getvalue())["name"] == newer.name


def test_nonregular_candidate_and_hardlink_are_rejected(archive, tmp_path):
    linked = tmp_path / "linked-archive"
    os.link(archive, linked)
    with pytest.raises(ValueError, match="regular"):
        exporter().export_request("latest", archive.parent, io.BytesIO())
    linked.unlink()
    newer = archive.parent / "knowledge-20990101T000000-deadbeef.tar.gz"
    newer.mkdir()
    with pytest.raises(ValueError, match="regular"):
        exporter().export_request("latest", archive.parent, io.BytesIO())


def test_get_writes_archive_in_bounded_chunks(tmp_path):
    source = tmp_path / "source"
    (source / "vault").mkdir(parents=True)
    (source / "data").mkdir()
    (source / "data" / "random.bin").write_bytes(os.urandom(2 * 1024 * 1024 + 71))
    archive = server_backup.create_backup(source, tmp_path / "backups")
    module = exporter()

    class Output(io.BytesIO):
        def write(self, data):
            assert len(data) <= module.CHUNK
            return super().write(data)

    output = Output()
    module.export_request("get " + archive.name, archive.parent, output)
    assert output.getvalue() == archive.read_bytes()


def test_latest_orders_utc_names_not_mtime_and_does_not_hide_corruption(archive):
    newer = archive.parent / "knowledge-20990101T000000-deadbeef.tar.gz"
    newer.write_bytes(b"damaged latest")
    os.utime(newer, (1, 1))
    (archive.parent / "knowledge-20990102T000000-deadbeef.tar.partial").write_bytes(b"unfinished")
    with pytest.raises(ValueError):
        exporter().export_request("latest", archive.parent, io.BytesIO())


@pytest.mark.parametrize("target", ["root", "archive", "sidecar"])
def test_symlinks_are_never_followed(archive, tmp_path, target):
    root = archive.parent
    if target == "root":
        link = tmp_path / "linked-root"
        source = root
        root = link
    else:
        link = archive if target == "archive" else Path(str(archive) + ".sha256")
        source = tmp_path / ("outside" + link.suffix)
        link.replace(source)
    try:
        link.symlink_to(source, target_is_directory=target == "root")
    except OSError:
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(ValueError):
        exporter().export_request("latest", root, io.BytesIO())


def test_cli_rejects_extra_args_without_accessing_root(capsys):
    assert exporter().main(["latest", "--root", "/etc"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err
