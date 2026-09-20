"""Check private Syncthing Vault configuration and optional live health."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

try:
    from scripts.syncthing_config import validate_config
except ModuleNotFoundError:  # direct execution from the repository root
    from syncthing_config import validate_config


def check_config(config: Path, vault: Path, device_ids: list[str]) -> dict:
    return validate_config(config, vault, device_ids)


def check_live(url: str, api_key: str, folder_id: str) -> dict:
    headers = {"X-API-Key": api_key}
    with httpx.Client(
        base_url=url.rstrip("/"), headers=headers, timeout=10, trust_env=False
    ) as client:
        system = client.get("/rest/system/status")
        system.raise_for_status()
        folder = client.get("/rest/db/status", params={"folder": folder_id})
        folder.raise_for_status()
    payload = folder.json()
    if payload.get("globalBytes", 0) != payload.get("localBytes", 0):
        raise ValueError("Syncthing folder hashes are not converged")
    return {
        "syncthing": "reachable",
        "folder": folder_id,
        "state": payload.get("state"),
        "global_bytes": payload.get("globalBytes"),
        "local_bytes": payload.get("localBytes"),
        "conflicts": payload.get("needBytes", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--device-id", action="append", default=[])
    parser.add_argument("--url")
    parser.add_argument("--api-key")
    args = parser.parse_args()
    try:
        result = check_config(args.config, args.vault, args.device_id)
        if args.url:
            if not args.api_key:
                raise ValueError("--url requires a local API key")
            result.update(check_live(args.url, args.api_key, result["folder"]))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, httpx.HTTPError) as error:
        print(f"Syncthing check failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
