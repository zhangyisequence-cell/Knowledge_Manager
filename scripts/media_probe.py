"""Explicit opt-in real media check, separate from offline regression tests."""
import argparse
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    args = parser.parse_args()
    start = time.monotonic()
    with httpx.Client(base_url=args.url, timeout=30, trust_env=False) as client:
        with args.file.open("rb") as source:
            response = client.post("/api/upload", files={"file": (args.file.name, source)})
        response.raise_for_status()
        job = response.json()
        while time.monotonic() - start < args.timeout:
            response = client.get(f"/api/jobs/{job['id']}")
            response.raise_for_status()
            job = response.json()
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.5)
    result = {"job": job, "elapsed_seconds": round(time.monotonic() - start, 2)}
    destination = ROOT / "runtime" / "media-probe.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if job["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
