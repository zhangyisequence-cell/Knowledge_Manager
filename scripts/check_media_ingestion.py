"""Exercise real local media ingestion and verify persisted transcription artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import httpx
import yaml
from check_chinese_transcription import evaluate_checks
from check_local_ai_ingestion import discover_database, read_item
from hf_snapshot_download import PINNED_REVISION

EXPECTED_MODEL = "Systran/faster-whisper-small"
SAMPLE_NAMES = tuple(f"chinese-validation.{suffix}" for suffix in ("wav", "mp4", "amr"))
WARNING = "> 自动转写，未经人工复核"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wait_for_job(client: httpx.Client, job: dict, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while job.get("status") not in {"succeeded", "failed"}:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Job did not finish within {timeout} seconds")
        time.sleep(min(0.5, remaining))
        response = client.get(f"/api/jobs/{job['id']}", timeout=min(30, remaining))
        response.raise_for_status()
        job = response.json()
    return job


def transcript_from_note(note_text: str) -> str:
    marker = "## 转写文本\n\n"
    if marker not in note_text:
        raise ValueError("Saved note has no transcript section")
    body = note_text.split(marker, 1)[1].split("\n\n## ", 1)[0]
    if body.startswith(WARNING + "\n\n"):
        body = body[len(WARNING) + 2 :]
    if not body:
        raise ValueError("Saved note transcript is empty")
    return body


def verify_job(job: dict, source: Path, output: Path, database: Path) -> dict:
    if job.get("status") != "succeeded" or not job.get("item_id") or not job.get("note_path"):
        raise ValueError(f"Media job did not succeed: {job.get('error') or job.get('status')}")
    note = Path(job["note_path"])
    if not note.is_absolute() or not note.is_file():
        raise ValueError("Saved note is not locally readable; run this check on the server")
    note_bytes = note.read_bytes()
    note_text = note_bytes.decode("utf-8").replace("\r\n", "\n")
    parts = note_text.split("---", 2)
    if len(parts) != 3:
        raise ValueError("Saved note has no YAML frontmatter")
    frontmatter = yaml.safe_load(parts[1])
    if not isinstance(frontmatter, dict):
        raise TypeError("Saved note frontmatter is invalid")

    source_bytes = source.read_bytes()
    source_hash = sha256(source_bytes)
    upload = Path(job.get("payload", {}).get("path", ""))
    hrefs = re.findall(r"\[附件 \d+\]\(([^)]+)\)", note_text)
    attachments = []
    for href in hrefs:
        relative = Path(unquote(href))
        attachment = (note.parent / relative).resolve()
        if relative.is_absolute() or not attachment.is_relative_to(note.parent.resolve()):
            raise ValueError("Attachment link escapes the note folder")
        stored = attachment.read_bytes()
        attachments.append({"path": str(attachment), "sha256": sha256(stored)})

    transcript = transcript_from_note(note_text)
    item = read_item(database, job["item_id"])
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        raise TypeError("SQLite item has no metadata object")
    checks = evaluate_checks(transcript)
    model = frontmatter.get("transcription_model")
    provenance_keys = ("transcription_review_required", "transcription_engine",
                       "transcription_model", "transcription_model_reference",
                       "transcription_model_revision", "transcription_decoding",
                       "transcription_runtime")
    provenance_ok = (
        frontmatter.get("transcription_review_required") is True
        and frontmatter.get("transcription_engine") == "faster-whisper"
        and frontmatter.get("transcription_decoding")
        == {"vad_filter": True, "beam_size": 5, "language": "auto"}
        and frontmatter.get("transcription_runtime")
        == {"device": "cpu", "compute_type": "int8"}
    )
    pinned_ok = model == EXPECTED_MODEL and frontmatter.get("transcription_model_revision") == PINNED_REVISION
    pipeline_checks = {
        "content_id_matches_job": frontmatter.get("content_id") == job["item_id"],
        "sqlite_id_matches_job": item.get("id") == job["item_id"],
        "sqlite_note_path_matches_job": item.get("_database_note_path") == str(note),
        "server_upload_matches_input": upload.is_absolute() and upload.is_file()
        and sha256(upload.read_bytes()) == source_hash,
        "vault_attachment_matches_input": any(row["sha256"] == source_hash for row in attachments),
        "transcript_persisted": bool(transcript),
        "sqlite_transcript_matches_note": item.get("transcript") == transcript,
        "sqlite_provenance_matches_note": all(
            metadata.get(key) == frontmatter.get(key) for key in provenance_keys),
        "generated_transcript_marked": provenance_ok,
        "expected_model_revision_pinned": pinned_ok,
        "visible_review_warning": WARNING in note_text,
    }
    (output / f"{source.stem}-{source.suffix[1:]}-note.md").write_bytes(note_bytes)
    return {
        "pipeline_passed": all(pipeline_checks.values()),
        "transcription_accuracy_passed": all(checks.values()),
        "pipeline_checks": pipeline_checks,
        "transcription_checks": checks,
        "transcript": transcript,
        "transcript_sha256": sha256(transcript.encode("utf-8")),
        "input_sha256": source_hash,
        "note_sha256": sha256(note_bytes),
        "frontmatter": frontmatter,
        "attachments": attachments,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--timeout", type=float, default=900, help="Seconds per job (600-7200)")
    args = parser.parse_args()
    if not 600 <= args.timeout <= 7200:
        parser.error("--timeout must be between 600 and 7200 seconds")
    sample_dir = args.sample_dir.expanduser().resolve()
    samples = [sample_dir / name for name in SAMPLE_NAMES]
    missing = [str(path) for path in samples if not path.is_file()]
    if missing:
        parser.error("missing required synthetic samples: " + ", ".join(missing))
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "media-ingestion-results.json"
    report = {
        "url": args.url,
        "results": [],
        "pipeline_passed": False,
        "transcription_accuracy_passed": False,
        "passed": False,
    }

    def save() -> None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    try:
        with httpx.Client(base_url=args.url.rstrip("/"), timeout=30, trust_env=False) as client:
            response = client.get("/api/health")
            response.raise_for_status()
            report["health"] = response.json()
            if report["health"].get("status") != "ok" or report["health"].get("storage_configured") is not True:
                raise ValueError("Health does not confirm configured storage")
            database = discover_database()
            report["database"] = str(database)
            for source in samples:
                row = {"sample": source.name, "pipeline_passed": False,
                       "transcription_accuracy_passed": False}
                report["results"].append(row)
                try:
                    with source.open("rb") as stream:
                        response = client.post("/api/upload", data={"title": f"合成媒体验收：{source.name}"},
                                               files={"file": (source.name, stream)})
                    response.raise_for_status()
                    submitted = response.json()
                    row["job"] = submitted
                    save()
                    job = wait_for_job(client, submitted, args.timeout)
                    row["job"] = job
                    row.update(verify_job(job, source, output, database))
                except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, yaml.YAMLError, sqlite3.Error) as error:
                    row["error"] = f"{type(error).__name__}: {error}"
                save()
                print(f"{source.name}: pipeline={row['pipeline_passed']}; accuracy={row['transcription_accuracy_passed']}")
        report["pipeline_passed"] = len(report["results"]) == 3 and all(
            row["pipeline_passed"] for row in report["results"])
        report["transcription_accuracy_passed"] = len(report["results"]) == 3 and all(
            row["transcription_accuracy_passed"] for row in report["results"])
        report["passed"] = report["pipeline_passed"] and report["transcription_accuracy_passed"]
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
    print(f"Evidence: {report_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
