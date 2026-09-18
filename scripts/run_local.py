"""Start an isolated, loopback-only pilot; credentials are read from the environment."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--vault", type=Path, default=ROOT / "runtime" / "vault")
    parser.add_argument("--data", type=Path, default=ROOT / "runtime" / "data")
    parser.add_argument("--enable-ai", action="store_true")
    args = parser.parse_args()
    if args.enable_ai and not (os.getenv("OPENAI_MODEL") and (
        os.getenv("KNOWLEDGE_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    )):
        parser.error("AI requires OPENAI_MODEL and KNOWLEDGE_OPENAI_BASE_URL (or OPENAI_BASE_URL)")
    os.environ["KNOWLEDGE_CONFIG"] = str(ROOT / "config.local.yaml")
    os.environ["OBSIDIAN_VAULT_DIR"] = str(args.vault.resolve())
    os.environ["KNOWLEDGE_DATA_DIR"] = str(args.data.resolve())
    os.environ["AI_ENABLED"] = str(args.enable_ai).lower()
    os.environ.setdefault("HF_HOME", str(ROOT / "runtime" / "model-cache"))
    from backend.config import get_config
    get_config.cache_clear()
    config = get_config()
    # Pilot always keeps original videos; user configuration cannot silently delete them.
    config.delete_video_after_ingest = False
    print(f"Pilot: http://127.0.0.1:{args.port}; AI: {args.enable_ai}")
    print(f"Vault: {config.vault_dir}")
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
