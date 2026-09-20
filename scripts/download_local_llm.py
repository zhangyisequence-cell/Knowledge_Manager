"""Verify or explicitly download the pinned local Qwen GGUF model."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

from hf_snapshot_download import download_verified

REPOSITORY = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
REVISION = "91cad51170dc346986eccefdc2dd33a9da36ead9"
FILENAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
MODEL_SIZE = 1_117_320_736
MODEL_SHA256 = "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"


def model_path(cache_dir: Path) -> Path:
    repository_dir = f"models--{REPOSITORY.replace('/', '--')}"
    return cache_dir / repository_dir / "snapshots" / REVISION / FILENAME


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_verified(path: Path) -> bool:
    try:
        return path.stat().st_size == MODEL_SIZE and _sha256(path) == MODEL_SHA256
    except FileNotFoundError:
        return False


def ensure_model(
    cache_dir: Path, *, allow_download: bool, endpoint: str | None = None
) -> Path:
    destination = model_path(cache_dir)
    if _is_verified(destination):
        return destination
    if not allow_download:
        raise FileNotFoundError(
            f"Verified local model is unavailable: {destination}. "
            "Re-run with --download to permit the pinned public download."
        )
    base_url = (
        endpoint or os.environ.get("HF_ENDPOINT") or "https://huggingface.co"
    ).rstrip("/")
    url = f"{base_url}/{REPOSITORY}/resolve/{REVISION}/{FILENAME}?download=true"
    return download_verified(
        url,
        destination,
        expected_size=MODEL_SIZE,
        expected_sha256=MODEL_SHA256,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument(
        "--download",
        action="store_true",
        help="Explicitly permit downloading the pinned public model",
    )
    args = parser.parse_args()
    print(ensure_model(args.cache_dir, allow_download=args.download))


if __name__ == "__main__":
    main()
