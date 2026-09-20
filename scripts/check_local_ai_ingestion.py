"""Run a synthetic, end-to-end provider-neutral AI ingestion acceptance check."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
import yaml

CHUNK_SIZE = 3_000
FACTS = {
    "beginning": {
        "record_id": "KJ-2026-104",
        "value": "林岚",
        "text": "项目记录KJ-2026-104：档案负责人为林岚，负责核对首批会议资料。",
    },
    "middle": {
        "record_id": "KJ-2026-287",
        "value": "23741元",
        "text": "项目记录KJ-2026-287：本季度资料整理预算核定为23741元，支出需逐项留痕。",
    },
    "tail": {
        "record_id": "KJ-2026-953",
        "value": "2026年10月17日",
        "text": "项目记录KJ-2026-953：最终归档日期定为2026年10月17日，完成后进行只读复核。",
    },
}


def make_sample() -> str:
    paragraph = (
        "团队按周整理访谈、会议记录与研究资料，复核标题、日期、责任人和附件。"
        "每次复核都记录发现的问题，并在下次整理时检查修正结果。"
    )
    first = FACTS["beginning"]["text"] + paragraph * 52
    middle = FACTS["middle"]["text"] + paragraph * 52
    third = paragraph * 52
    text = first + middle + third + FACTS["tail"]["text"]
    if len(text) <= CHUNK_SIZE * 3:
        raise AssertionError("Synthetic sample does not contain three complete chunks")
    if not (
        text.index(FACTS["beginning"]["text"])
        < CHUNK_SIZE
        <= text.index(FACTS["middle"]["text"])
        < CHUNK_SIZE * 2
        and text.index(FACTS["tail"]["text"]) >= CHUNK_SIZE * 3
    ):
        raise AssertionError("Synthetic facts are not placed in beginning/middle/tail chunks")
    return text


def _parse_environment_file(path: Path) -> dict[str, str]:
    values = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in {"KNOWLEDGE_CONFIG", "KNOWLEDGE_DATA_DIR"}:
            values[key] = value.strip().strip('"').strip("'")
    return values


def discover_database() -> Path:
    settings = _parse_environment_file(Path("/etc/knowledge-manager/server.env"))
    config_value = os.environ.get("KNOWLEDGE_CONFIG") or settings.get("KNOWLEDGE_CONFIG")
    config_path = (
        Path(config_value).expanduser().resolve()
        if config_value
        else (Path(__file__).parents[1] / "vendor/knowledge-inbox/config.yaml").resolve()
    )
    raw = yaml.safe_load(config_path.read_text("utf-8")) if config_path.is_file() else {}
    raw = raw or {}
    data_value = (
        os.environ.get("KNOWLEDGE_DATA_DIR")
        or settings.get("KNOWLEDGE_DATA_DIR")
        or raw.get("data_dir", "./data")
    )
    data_dir = Path(data_value).expanduser()
    if not data_dir.is_absolute():
        data_dir = (config_path.parent / data_dir).resolve()
    database = Path(raw.get("database_path", "./data/knowledge.sqlite")).expanduser()
    if not database.is_absolute():
        database = (data_dir / database.name).resolve()
    if not database.is_file():
        raise FileNotFoundError(f"Cannot read deployed item metadata database: {database}")
    return database


def read_item(database: Path, item_id: str) -> dict:
    uri = f"file:{quote(database.as_posix(), safe='/:')}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=20) as connection:
        row = connection.execute(
            "SELECT data, note_path FROM items WHERE id=?", (item_id,)
        ).fetchone()
    if row is None:
        raise ValueError(f"Succeeded item is missing from SQLite: {item_id}")
    item = json.loads(row[0])
    item["_database_note_path"] = row[1]
    return item


def _analysis_text(item: dict) -> str:
    metadata = item.get("metadata") or {}
    selected = {
        "summary": item.get("summary"),
        "category": item.get("category"),
        "tags": item.get("tags"),
        "keywords": item.get("keywords"),
        "core_points": metadata.get("core_points"),
        "key_data": metadata.get("key_data"),
        "actions": metadata.get("actions"),
        "evidence": metadata.get("evidence"),
    }
    return json.dumps(selected, ensure_ascii=False)


def verify_item(
    item: dict,
    job: dict,
    sample: str,
    note_text: str,
    *,
    expected_provider: str | None = None,
    expected_model: str | None = None,
) -> dict:
    if item.get("id") != job.get("item_id"):
        raise ValueError("SQLite item id does not match the completed job")
    if item.get("source_type") != "text":
        raise ValueError("SQLite item source_type is not text")
    if item.get("raw_content") != sample:
        raise ValueError("SQLite item raw_content does not match the submitted input")
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        raise TypeError("SQLite item has no metadata object")
    coverage = metadata.get("coverage")
    chunks = coverage.get("chunks") if isinstance(coverage, dict) else None
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("AI coverage chunk metadata is unavailable; coverage is unverified")

    expected_start = 0
    chunk_checks = []
    for index, chunk in enumerate(chunks, 1):
        if not isinstance(chunk, dict):
            raise TypeError("AI coverage contains a non-object chunk")
        expected_id = f"chunk-{index:04d}"
        start, end = chunk.get("start"), chunk.get("end")
        valid = (
            chunk.get("id") == expected_id
            and type(start) is int
            and type(end) is int
            and start == expected_start
            and start <= end <= len(sample)
            and (end - start == CHUNK_SIZE or end == len(sample))
        )
        chunk_checks.append({"chunk": chunk, "valid": valid})
        if not valid:
            raise ValueError(f"Coverage is discontinuous or invalid at {expected_id}: {chunk}")
        expected_start = end
    if expected_start != len(sample):
        raise ValueError(
            f"Coverage ends at {expected_start}, but input contains {len(sample)} characters"
        )
    if coverage.get("characters") != len(sample):
        raise ValueError("Coverage character count does not match the submitted input")
    if coverage.get("total_chunks") != len(chunks):
        raise ValueError("Coverage total_chunks does not match the chunk list")
    if coverage.get("processed_chunks") != len(chunks):
        raise ValueError("Not every chunk was processed")
    if metadata.get("complete") is not True:
        raise ValueError("AI metadata does not mark the analysis complete")
    if metadata.get("analysis_mode") != "ai":
        raise ValueError(f"Expected analysis_mode=ai, got {metadata.get('analysis_mode')!r}")
    model = metadata.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("AI metadata has no non-empty model")
    provider = metadata.get("analysis_provider")
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("AI metadata has no non-empty analysis provider")
    if expected_provider is not None and provider != expected_provider:
        raise ValueError("AI metadata provider does not match expected provider")
    if expected_model is not None and model != expected_model:
        raise ValueError("AI metadata model does not match expected model")

    analysis_text = _analysis_text(item)
    fact_checks = {
        name: {
            "record_id": fact["record_id"] in analysis_text,
            "value": fact["value"] in analysis_text,
        }
        for name, fact in FACTS.items()
    }
    if not all(all(checks.values()) for checks in fact_checks.values()):
        raise ValueError(f"AI extraction missed synthetic facts: {fact_checks}")

    evidence = metadata.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("AI produced no grounded evidence")
    chunks_by_id = {chunk["id"]: chunk for chunk in chunks}
    evidence_checks = []
    grounded_facts = {name: False for name in FACTS}
    for entry in evidence:
        if not isinstance(entry, dict):
            raise TypeError("Evidence entry is not an object")
        chunk = chunks_by_id.get(entry.get("chunk_id"))
        start, end, quote_text = entry.get("start"), entry.get("end"), entry.get("quote")
        valid = (
            chunk is not None
            and type(start) is int
            and type(end) is int
            and isinstance(quote_text, str)
            and bool(quote_text)
            and chunk["start"] <= start < end <= chunk["end"]
            and sample[start:end] == quote_text
            and entry.get("source_url") is None
        )
        note_marker = (
            f"{entry.get('chunk_id')}，字符 {start}-{end}"
            if type(start) is int and type(end) is int
            else ""
        )
        rendered = valid and quote_text in note_text and note_marker in note_text
        evidence_checks.append({"entry": entry, "valid": valid, "rendered": rendered})
        if not valid or not rendered:
            raise ValueError(f"Evidence is not grounded/rendered: {entry}")
        for name, fact in FACTS.items():
            if fact["record_id"] in quote_text and fact["value"] in quote_text:
                grounded_facts[name] = True
    if not all(grounded_facts.values()):
        raise ValueError(f"Key facts lack grounded evidence: {grounded_facts}")

    frontmatter_parts = note_text.split("---", 2)
    if len(frontmatter_parts) != 3:
        raise ValueError("Saved note has no YAML frontmatter")
    frontmatter = yaml.safe_load(frontmatter_parts[1])
    if not isinstance(frontmatter, dict):
        raise TypeError("Saved note frontmatter is invalid")
    if frontmatter.get("content_id") != job.get("item_id"):
        raise ValueError("Saved note content_id does not match the job")
    if frontmatter.get("analysis_mode") != "ai" or frontmatter.get("source") != "text":
        raise ValueError("Saved note does not identify AI analysis and text source")
    if frontmatter.get("analysis_complete") is not True:
        raise ValueError("Saved note frontmatter does not mark analysis complete")
    if frontmatter.get("analysis_provider") != provider:
        raise ValueError("Saved note frontmatter analysis_provider does not match metadata")
    if frontmatter.get("analysis_model") != model:
        raise ValueError("Saved note frontmatter analysis_model does not match metadata")
    separator = "# 原始内容\n\n"
    if separator not in note_text:
        raise ValueError("Saved note has no original-content section")
    saved_original = note_text.split(separator, 1)[1].rstrip("\n")
    if saved_original != sample:
        raise ValueError("Saved original content is not byte-for-byte equivalent as UTF-8 text")

    return {
        "passed": True,
        "analysis_mode": metadata.get("analysis_mode"),
        "analysis_provider": provider,
        "model": metadata.get("model"),
        "coverage": coverage,
        "chunk_checks": chunk_checks,
        "fact_checks": fact_checks,
        "evidence_checks": evidence_checks,
        "grounded_facts": grounded_facts,
        "original_content_preserved": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=900, help="Job timeout in seconds")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 7200:
        parser.error("--timeout must be between 1 and 7200 seconds")

    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    sample = make_sample()
    input_path = output / "local-ai-long-input.txt"
    input_path.write_text(sample, encoding="utf-8")
    report_path = output / "local-ai-ingestion-results.json"
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "url": args.url,
        "input": {
            "path": str(input_path),
            "characters": len(sample),
            "sha256": hashlib.sha256(sample.encode()).hexdigest(),
            "facts": FACTS,
        },
        "passed": False,
        "status": "not_started",
    }

    def save() -> None:
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if "job" in report:
            (output / "local-ai-job.json").write_text(
                json.dumps(report["job"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    save()
    try:
        with httpx.Client(base_url=args.url.rstrip("/"), timeout=30, trust_env=False) as client:
            response = client.get("/api/health")
            response.raise_for_status()
            health = response.json()
            report["health"] = health
            if health.get("status") != "ok" or health.get("storage_configured") is not True:
                raise ValueError("Health does not confirm an operational configured server")
            if health.get("ai_enabled") is not True:
                raise ValueError("Health does not report ai_enabled=true; refusing rules fallback")
            database = discover_database()
            report["database"] = str(database)

            response = client.post(
                "/api/ingest",
                json={"title": "合成本地AI长文验收", "text": sample, "source_type": "text"},
            )
            response.raise_for_status()
            job = response.json()
            report.update(job=job, status=job.get("status", "unknown"))
            save()
            deadline = time.monotonic() + args.timeout
            while job.get("status") not in {"succeeded", "failed"}:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Job did not finish within {args.timeout} seconds")
                time.sleep(min(0.5, remaining))
                response = client.get(
                    f"/api/jobs/{job['id']}", timeout=min(30, remaining)
                )
                response.raise_for_status()
                job = response.json()
                report.update(job=job, status=job.get("status", "unknown"))
                save()
        if job.get("status") != "succeeded":
            raise ValueError(f"Ingestion job failed: {job.get('error') or 'unknown error'}")
        if not job.get("item_id") or not job.get("note_path"):
            raise ValueError("Succeeded job has no item_id/note_path")
        item = read_item(database, job["item_id"])
        (output / "local-ai-item.json").write_text(
            json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        note_path = Path(job["note_path"])
        if not note_path.is_absolute() or not note_path.is_file():
            raise ValueError("Saved note is not locally readable; run this check on the server")
        if item.get("_database_note_path") != str(note_path):
            raise ValueError("SQLite and job disagree about the saved note path")
        note_bytes = note_path.read_bytes()
        note_text = note_bytes.decode("utf-8").replace("\r\n", "\n")
        (output / "local-ai-note.md").write_bytes(note_bytes)
        report["verification"] = verify_item(item, job, sample, note_text)
        report["note"] = {
            "source_path": str(note_path),
            "evidence_copy": str(output / "local-ai-note.md"),
            "sha256": hashlib.sha256(note_bytes).hexdigest(),
        }
        report["passed"] = True
        report["status"] = "verified"
    except (
        httpx.HTTPError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        sqlite3.Error,
        yaml.YAMLError,
    ) as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["status"] = "failed"
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
    print(f"Evidence: {report_path}")
    if report["passed"]:
        print("Local AI ingestion acceptance passed")
        return 0
    print(f"Local AI ingestion acceptance failed: {report.get('error', 'unknown error')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
