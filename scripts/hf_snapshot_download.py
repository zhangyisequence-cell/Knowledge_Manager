"""Download one pinned Hugging Face snapshot through direct, verified GETs.

Manifest sizes, Git blob IDs, and the LFS SHA-256 come from the repository API:
https://huggingface.co/api/models/Systran/faster-whisper-small/revision/536b0662742c02347bc0e980a01041f333bce120?blobs=true
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import urllib.request
from pathlib import Path
from urllib.request import Request

REPOSITORY = "Systran/faster-whisper-small"
PINNED_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
MANIFEST = {
    "config.json": {"size": 2370, "git_blob": "e5047537059bd8f182d9ca64c470201585015187"},
    "model.bin": {
        "size": 483546902,
        "git_blob": "504a7fe023f19460564a623887a9f77ea53ac708",
        "sha256": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
    },
    "tokenizer.json": {"size": 2203239, "git_blob": "7818adb6de9fa3064d3ff81226fdd675be1f6344"},
    "vocabulary.txt": {"size": 459861, "git_blob": "c9074644d9d1205686f16d411564729461324b75"},
}


def select_proxies(environment: dict[str, str], registry: dict[str, str]) -> dict[str, str]:
    proxies = dict(environment)
    if not any(key in proxies for key in ("http", "https")):
        proxies.update(registry)
    return proxies


def _open(request: Request, timeout: int):
    environment = urllib.request.getproxies_environment()
    registry_reader = getattr(urllib.request, "getproxies_registry", None)
    registry = registry_reader() if registry_reader else {}
    proxies = select_proxies(environment, registry)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    return opener.open(request, timeout=timeout)


def _digest(path: Path, algorithm: str, prefix: bytes = b"") -> str:
    digest = hashlib.new(algorithm)
    digest.update(prefix)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_git_blob(path: Path, expected: str) -> None:
    prefix = b"blob " + str(path.stat().st_size).encode("ascii") + b"\0"
    actual = _digest(path, "sha1", prefix)
    if actual != expected:
        raise ValueError(f"Git blob mismatch for {path.name}: expected {expected}, got {actual}")


def download_verified(url: str, destination: Path, *, expected_size: int,
                      expected_sha256: str | None = None,
                      expected_git_blob: str | None = None,
                      timeout: int = 60) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > expected_size:
        partial.unlink()
        offset = 0
    headers = {"User-Agent": "KnowledgeManager/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = Request(url, headers=headers)
    with _open(request, timeout=timeout) as response:
        resumed = offset and getattr(response, "status", None) == 206
        if resumed:
            content_range = response.headers.get("Content-Range", "")
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", content_range)
            actual_start = int(match.group(1)) if match else None
            if actual_start != offset:
                raise ValueError(
                    f"Content-Range start mismatch for {destination.name}: "
                    f"expected {offset}, got {actual_start}"
                )
        mode = "ab" if resumed else "wb"
        written = offset if resumed else 0
        with partial.open(mode) as stream:
            while written < expected_size:
                chunk = response.read(min(1024 * 1024, expected_size - written))
                if not chunk:
                    break
                stream.write(chunk)
                written += len(chunk)
            if written == expected_size and response.read(1):
                stream.close()
                partial.unlink(missing_ok=True)
                raise ValueError(f"Response exceeds expected size for {destination.name}")
            stream.flush()
            os.fsync(stream.fileno())
    actual_size = partial.stat().st_size
    if actual_size != expected_size:
        raise ValueError(f"Size mismatch for {destination.name}: expected {expected_size}, got {actual_size}")
    if expected_sha256:
        actual = _digest(partial, "sha256")
        if actual != expected_sha256:
            partial.unlink(missing_ok=True)
            raise ValueError(
                f"SHA-256 mismatch for {destination.name}: expected {expected_sha256}, got {actual}"
            )
    if expected_git_blob:
        try:
            verify_git_blob(partial, expected_git_blob)
        except ValueError:
            partial.unlink(missing_ok=True)
            raise
    partial.replace(destination)
    return destination


def ensure_snapshot(cache_dir: Path, revision: str, *, allow_download: bool,
                    endpoint: str | None = None) -> Path:
    if revision != PINNED_REVISION:
        raise ValueError(f"No verified manifest is available for revision {revision}")
    snapshot = cache_dir / f"models--{REPOSITORY.replace('/', '--')}" / "snapshots" / revision
    endpoint = (endpoint or os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
    for filename, metadata in MANIFEST.items():
        destination = snapshot / filename
        try:
            if destination.stat().st_size != metadata["size"]:
                raise ValueError("size")
            if "sha256" in metadata:
                if _digest(destination, "sha256") != metadata["sha256"]:
                    raise ValueError("sha256")
            else:
                verify_git_blob(destination, metadata["git_blob"])
            continue
        except (FileNotFoundError, ValueError):
            if not allow_download:
                raise FileNotFoundError(f"Verified model file is unavailable: {destination}")
        url = f"{endpoint}/{REPOSITORY}/resolve/{revision}/{filename}?download=true"
        download_verified(
            url,
            destination,
            expected_size=metadata["size"],
            expected_sha256=metadata.get("sha256"),
            expected_git_blob=None if "sha256" in metadata else metadata["git_blob"],
        )
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--download', action='store_true', help='Permit public model downloads')
    args = parser.parse_args()
    print(ensure_snapshot(args.cache_dir, PINNED_REVISION, allow_download=args.download))


if __name__ == '__main__':
    main()
