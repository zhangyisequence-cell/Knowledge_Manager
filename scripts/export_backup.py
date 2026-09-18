"""Restricted read-only backup export protocol for an SSH forced command."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

BACKUP_ROOT = Path("/var/backups/knowledge-manager")
NAME = re.compile(r"knowledge-([0-9]{8}T[0-9]{6})-[0-9a-f]{8}\.tar\.gz")
CHUNK = 1024 * 1024


def _valid_name(name: str) -> bool:
    match = NAME.fullmatch(name)
    if not match:
        return False
    try:
        datetime.strptime(match[1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return True


def _open_regular(path: Path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400):
        raise ValueError("Backup path is not a single regular file")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_BINARY", 0))
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)):
            raise ValueError("Backup file changed while opening")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def _hash(stream) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(CHUNK), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _verify_manifest(stream):
    actual, manifest, seen = {}, None, set()
    with tarfile.open(fileobj=stream, mode="r|gz") as bundle:
        for member in bundle:
            if not member.isfile() or member.name in seen:
                raise ValueError("Backup contains nonregular or duplicate members")
            seen.add(member.name)
            content = bundle.extractfile(member)
            with content:
                if member.name == "manifest.json":
                    if manifest is not None or member.size > 16 * 1024 * 1024:
                        raise ValueError("Invalid backup manifest")
                    manifest = json.loads(content.read(16 * 1024 * 1024 + 1))
                    continue
                parts = PurePosixPath(member.name).parts
                if (member.name in actual or "\\" in member.name or ":" in member.name
                        or len(parts) < 2 or parts[0] not in {"vault", "data", "config"}
                        or any(p in {"", ".", ".."} for p in member.name.split("/"))):
                    raise ValueError("Unsafe or duplicate backup member")
                actual[member.name] = {"size": member.size, "sha256": _hash(content)}
    if (not isinstance(manifest, dict) or manifest.get("version") != 1
            or not isinstance(manifest.get("files"), dict)
            or manifest["files"] != actual):
        raise ValueError("Backup manifest does not match archive contents")


def export_request(request: str, root: Path, output):
    """Validate before emitting anything; root is injectable only for local tests.

    Latest means the largest UTC timestamp in the published filename, then the
    hexadecimal suffix as a deterministic tie-breaker. Filesystem mtime is ignored.
    A corrupt latest candidate is an error, never a reason to export an older one.
    """
    if request == "latest":
        name = None
    elif request.startswith("get ") and _valid_name(request[4:]):
        name = request[4:]
    else:
        raise ValueError("Allowed requests: latest or get <published-backup-name>")
    try:
        root = Path(root).absolute()
        if root.is_symlink() or root.resolve(strict=True) != root or not root.is_dir():
            raise ValueError("Backup root is not a real directory")
        if name is None:
            candidates = [p.name for p in root.iterdir() if _valid_name(p.name)]
            if not candidates:
                raise ValueError("No published backups")
            name = max(candidates)
        archive = root / name
        with _open_regular(archive) as source, _open_regular(Path(str(archive) + ".sha256")) as sidecar:
            info = os.fstat(source.fileno())
            checksum = sidecar.read(256)
            expected = re.fullmatch(
                rb"([0-9a-f]{64})  " + re.escape(name.encode("ascii")) + rb"\r?\n", checksum
            )
            if not expected or sidecar.read(1):
                raise ValueError("Invalid backup checksum sidecar")
            sha256 = _hash(source)
            if sha256 != expected[1].decode("ascii"):
                raise ValueError("Backup checksum mismatch")
            source.seek(0)
            _verify_manifest(source)
            source.seek(0)
            current = os.fstat(source.fileno())
            if (info.st_size, info.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
                raise ValueError("Backup changed during verification")
            if request == "latest":
                output.write(json.dumps({"name": name, "size": info.st_size,
                                         "sha256": sha256}).encode("utf-8") + b"\n")
            else:
                remaining = info.st_size
                while remaining:
                    chunk = source.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ValueError("Backup truncated while streaming")
                    output.write(chunk)
                    remaining -= len(chunk)
                current = os.fstat(source.fileno())
                if (info.st_size, info.st_mtime_ns) != (current.st_size, current.st_mtime_ns):
                    raise ValueError("Backup changed while streaming")
    except (OSError, tarfile.TarError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Backup export validation or I/O failed") from error


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        if len(argv) != 1:
            raise ValueError("Exactly one request argument is required")
        export_request(argv[0], BACKUP_ROOT, sys.stdout.buffer)
        sys.stdout.buffer.flush()
        return 0
    except (ValueError, OSError) as error:
        print(f"Backup export failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
