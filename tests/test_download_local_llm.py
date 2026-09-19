import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
HELPER_SPEC = importlib.util.spec_from_file_location(
    "hf_snapshot_download", ROOT / "scripts" / "hf_snapshot_download.py"
)
helper = importlib.util.module_from_spec(HELPER_SPEC)
HELPER_SPEC.loader.exec_module(helper)
sys.modules["hf_snapshot_download"] = helper

MODULE_SPEC = importlib.util.spec_from_file_location(
    "download_local_llm", ROOT / "scripts" / "download_local_llm.py"
)
local_llm = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(local_llm)


def test_manifest_is_pinned_to_verified_official_qwen_file():
    assert local_llm.REPOSITORY == "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
    assert local_llm.REVISION == "91cad51170dc346986eccefdc2dd33a9da36ead9"
    assert local_llm.FILENAME == "qwen2.5-1.5b-instruct-q4_k_m.gguf"
    assert local_llm.MODEL_SIZE == 1_117_320_736
    assert local_llm.MODEL_SHA256 == (
        "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e"
    )


def test_missing_model_requires_explicit_download(tmp_path, monkeypatch):
    called = False

    def unexpected_download(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(local_llm, "download_verified", unexpected_download)

    with pytest.raises(FileNotFoundError, match="--download"):
        local_llm.ensure_model(tmp_path, allow_download=False)

    assert called is False


def test_existing_complete_file_is_hash_verified_without_download(tmp_path, monkeypatch):
    payload = b"verified-small-fixture"
    monkeypatch.setattr(local_llm, "MODEL_SIZE", len(payload))
    monkeypatch.setattr(local_llm, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    target = local_llm.model_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    monkeypatch.setattr(
        local_llm,
        "download_verified",
        lambda *args, **kwargs: pytest.fail("valid existing model must not download"),
    )

    assert local_llm.ensure_model(tmp_path, allow_download=False) == target


def test_explicit_download_reuses_hf_endpoint_and_fixed_manifest(tmp_path, monkeypatch):
    captured = {}

    def fake_download(url, destination, **kwargs):
        captured.update(url=url, destination=destination, **kwargs)
        return destination

    monkeypatch.setattr(local_llm, "download_verified", fake_download)
    target = local_llm.ensure_model(
        tmp_path,
        allow_download=True,
        endpoint="https://hf-mirror.example/base/",
    )

    assert target == local_llm.model_path(tmp_path)
    assert captured == {
        "url": (
            "https://hf-mirror.example/base/"
            f"{local_llm.REPOSITORY}/resolve/{local_llm.REVISION}/{local_llm.FILENAME}"
            "?download=true"
        ),
        "destination": target,
        "expected_size": local_llm.MODEL_SIZE,
        "expected_sha256": local_llm.MODEL_SHA256,
    }
