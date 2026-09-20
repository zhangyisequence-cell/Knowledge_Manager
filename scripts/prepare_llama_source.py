"""Verify and unpack the exact official llama.cpp source archive; no network/build."""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

COMMIT = "b29c606e28a01b1bc8c1351026a0fa6e616bf6c4"
ARCHIVE_ROOT = "llama.cpp-" + COMMIT
ARCHIVE_SIZE = 37_437_459
ARCHIVE_SHA256 = "03fb04316eb32a7b7347004a79a8dd60531c02f5a064d853fed3b53828951723"
CHUNK = 1024 * 1024


def prepare_source(archive: Path, destination: Path):
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Source destination already exists; refusing overwrite")
    if destination.parent.resolve(strict=True) != destination.parent:
        raise ValueError("Source destination has a linked ancestor")
    flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_BINARY", 0))
    if archive.is_symlink():
        raise ValueError("Source archive cannot be a symlink")
    with os.fdopen(os.open(archive, flags), "rb") as source, tempfile.TemporaryFile(
        dir=destination.parent
    ) as snapshot:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != ARCHIVE_SIZE:
            raise ValueError("Source archive is not a regular file of the pinned size")
        # Extract the private snapshot we hashed, not a path another user can
        # replace or modify after validation while this process is running as root.
        digest, size = hashlib.sha256(), 0
        while chunk := source.read(min(CHUNK, ARCHIVE_SIZE + 1 - size)):
            size += len(chunk)
            if size > ARCHIVE_SIZE:
                raise ValueError("Source archive grew beyond the pinned size")
            snapshot.write(chunk)
            digest.update(chunk)
        if size != ARCHIVE_SIZE or digest.hexdigest() != ARCHIVE_SHA256:
            raise ValueError("Source archive SHA-256/size does not match the pinned official archive")
        snapshot.seek(0)
        with tarfile.open(fileobj=snapshot, mode="r:gz") as bundle:
            entries, seen, expanded = [], set(), 0
            for member in bundle:
                parts = PurePosixPath(member.name).parts
                if (not parts or parts[0] != ARCHIVE_ROOT
                        or member.name.rstrip("/") != "/".join(parts)
                        or any(part in {".", ".."} for part in parts)
                        or "\\" in member.name or ":" in member.name
                        or not (member.isfile() or member.isdir())
                        or member.name in seen
                        or (len(parts) == 1 and not member.isdir())):
                    raise ValueError("Unsafe source archive member or unexpected top-level root")
                seen.add(member.name)
                expanded += member.size
                if len(seen) > 10_000 or expanded > 256 * 1024 * 1024:
                    raise ValueError("Source archive exceeds the pinned release's extraction limits")
                if len(parts) > 1:
                    entries.append((member, Path(*parts[1:])))
            if not any(relative == Path("CMakeLists.txt") and member.isfile()
                       for member, relative in entries):
                raise ValueError("Source archive lacks CMakeLists.txt")
            destination.mkdir(mode=0o755)
            for member, relative in entries:
                target = destination / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o755)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                    with bundle.extractfile(member) as content, target.open("xb") as output:
                        shutil.copyfileobj(content, output, length=CHUNK)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(prepare_source(args.archive, args.destination))


if __name__ == "__main__":
    main()
