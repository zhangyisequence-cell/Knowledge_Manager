"""Redacted live acceptance check for grounded DeepSeek ingestion."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

try:
    from scripts.check_local_ai_ingestion import (
        discover_database,
        make_sample,
        read_item,
        verify_item,
    )
except ModuleNotFoundError:
    from check_local_ai_ingestion import (
        discover_database,
        make_sample,
        read_item,
        verify_item,
    )


def verify_deepseek_item(item: dict, job: dict, sample: str, note_text: str, model: str) -> dict:
    return verify_item(
        item,
        job,
        sample,
        note_text,
        expected_provider="deepseek",
        expected_model=model,
    )


def _redacted_health(value: object) -> dict:
    if not isinstance(value, dict):
        return {"type": type(value).__name__}
    return {
        key: value.get(key)
        for key in ("status", "storage_configured", "ai_enabled")
        if key in value
    }


def run(url: str, timeout: float, output: Path) -> int:
    model = os.getenv("OPENAI_MODEL", "")
    base_url = os.getenv("KNOWLEDGE_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    if os.getenv("AI_ENABLED", "").lower() != "true" or not model or not base_url or not os.getenv("OPENAI_API_KEY"):
        raise ValueError("DeepSeek acceptance requires local AI_ENABLED, model, base URL and key")
    sample = make_sample()
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "provider": "deepseek",
        "model": model,
        "input": {"characters": len(sample), "sha256": hashlib.sha256(sample.encode()).hexdigest()},
        "passed": False,
    }
    started = time.monotonic()
    with httpx.Client(base_url=url.rstrip("/"), timeout=30, trust_env=False) as client:
        health_response = client.get("/api/health")
        health_response.raise_for_status()
        health = health_response.json()
        report["health"] = _redacted_health(health)
        if health.get("status") != "ok" or health.get("storage_configured") is not True or health.get("ai_enabled") is not True:
            raise ValueError("health gate failed")
        database = discover_database()
        response = client.post(
            "/api/ingest",
            json={"title": "DeepSeek grounded acceptance", "text": sample, "source_type": "text"},
        )
        response.raise_for_status()
        job = response.json()
        deadline = time.monotonic() + timeout
        while job.get("status") not in {"succeeded", "failed"}:
            if time.monotonic() >= deadline:
                raise TimeoutError("ingestion timeout")
            time.sleep(0.5)
            job_response = client.get(f"/api/jobs/{job['id']}")
            job_response.raise_for_status()
            job = job_response.json()
    if job.get("status") != "succeeded" or not job.get("item_id") or not job.get("note_path"):
        raise ValueError("ingestion did not succeed")
    item = read_item(database, job["item_id"])
    note_path = Path(job["note_path"])
    if not note_path.is_file():
        raise ValueError("saved note is unavailable")
    verification = verify_deepseek_item(item, job, sample, note_path.read_text("utf-8"), model)
    report["verification"] = {
        "coverage": verification["coverage"],
        "grounded_facts": verification["grounded_facts"],
        "original_content_preserved": verification["original_content_preserved"],
        "analysis_provider": verification["analysis_provider"],
    }
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["passed"] = True
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args.url, args.timeout, args.output)
    except Exception as error:  # noqa: BLE001 - report only a non-sensitive error class
        report = {
            "passed": False,
            "error_type": type(error).__name__,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"DeepSeek acceptance failed: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
