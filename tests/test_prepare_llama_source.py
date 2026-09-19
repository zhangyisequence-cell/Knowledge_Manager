"""Real tar fixtures for the offline pinned-source boundary; no builds/network."""
import hashlib
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_llama_source.py"


def helper():
    spec = importlib.util.spec_from_file_location("prepare_llama_source", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_archive(tmp_path, module, entries):
    path = tmp_path / "source.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, kind, data in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.mode = 0o7777
            if kind == tarfile.SYMTYPE:
                member.linkname = "/etc"
            if kind == tarfile.LNKTYPE:
                member.linkname = module.ARCHIVE_ROOT + "/CMakeLists.txt"
            if kind == tarfile.REGTYPE:
                member.size = len(data)
            archive.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return path


def pin_fixture(monkeypatch, module, path):
    monkeypatch.setattr(module, "ARCHIVE_SIZE", path.stat().st_size)
    monkeypatch.setattr(module, "ARCHIVE_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())


def test_validated_archive_unpacks_only_under_destination(tmp_path, monkeypatch):
    module = helper()
    path = fixture_archive(tmp_path, module, [
        (module.ARCHIVE_ROOT, tarfile.DIRTYPE, b""),
        (module.ARCHIVE_ROOT + "/CMakeLists.txt", tarfile.REGTYPE, b"project(llama)\n"),
        (module.ARCHIVE_ROOT + "/src/main.cpp", tarfile.REGTYPE, b"synthetic source\n"),
    ])
    pin_fixture(monkeypatch, module, path)
    destination = tmp_path / "unpacked"
    module.prepare_source(path, destination)
    assert (destination / "CMakeLists.txt").read_bytes() == b"project(llama)\n"
    assert (destination / "src/main.cpp").read_bytes() == b"synthetic source\n"


def test_wrong_digest_is_rejected_before_creating_destination(tmp_path, monkeypatch):
    module = helper()
    path = fixture_archive(tmp_path, module, [])
    monkeypatch.setattr(module, "ARCHIVE_SIZE", path.stat().st_size)
    destination = tmp_path / "unpacked"
    with pytest.raises(ValueError, match="SHA-256"):
        module.prepare_source(path, destination)
    assert not destination.exists()


@pytest.mark.parametrize("suffix,kind", [
    ("/../escape", tarfile.REGTYPE), ("//escape", tarfile.REGTYPE),
    ("/./escape", tarfile.REGTYPE), ("/C:/escape", tarfile.REGTYPE),
    ("/link", tarfile.SYMTYPE), ("/hard", tarfile.LNKTYPE),
    ("/device", tarfile.CHRTYPE), ("/pipe", tarfile.FIFOTYPE),
])
def test_unsafe_members_rejected_even_in_hash_verified_input(tmp_path, monkeypatch, suffix, kind):
    module = helper()
    path = fixture_archive(tmp_path, module, [(module.ARCHIVE_ROOT + suffix, kind, b"unsafe")])
    pin_fixture(monkeypatch, module, path)
    destination = tmp_path / "unpacked"
    with pytest.raises(ValueError):
        module.prepare_source(path, destination)
    assert not destination.exists()


def test_wrong_archive_root_rejected(tmp_path, monkeypatch):
    module = helper()
    path = fixture_archive(tmp_path, module, [("other/CMakeLists.txt", tarfile.REGTYPE, b"x")])
    pin_fixture(monkeypatch, module, path)
    with pytest.raises(ValueError):
        module.prepare_source(path, tmp_path / "unpacked")


def test_existing_destination_is_not_overwritten(tmp_path):
    module = helper()
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(ValueError, match="destination"):
        module.prepare_source(tmp_path / "absent.tar.gz", destination)
