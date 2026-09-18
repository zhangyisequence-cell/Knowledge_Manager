import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "check_local_ai_ingestion.py"
SPEC = importlib.util.spec_from_file_location("check_local_ai_ingestion", MODULE_PATH)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def make_fixture():
    sample = checker.make_sample()
    chunks = [
        {
            "id": f"chunk-{index + 1:04d}",
            "start": start,
            "end": min(start + checker.CHUNK_SIZE, len(sample)),
        }
        for index, start in enumerate(range(0, len(sample), checker.CHUNK_SIZE))
    ]
    evidence = []
    for name, fact in checker.FACTS.items():
        start = sample.index(fact["text"])
        chunk = chunks[start // checker.CHUNK_SIZE]
        evidence.append(
            {
                "claim": f"{fact['record_id']} 的关键值为 {fact['value']}",
                "quote": fact["text"],
                "chunk_id": chunk["id"],
                "start": start,
                "end": start + len(fact["text"]),
                "source_url": None,
                "fact_name": name,
            }
        )
    item = {
        "id": "fixture-item",
        "source_type": "text",
        "source_url": None,
        "title": "合成本地AI长文验收",
        "raw_content": sample,
        "transcript": None,
        "summary": "；".join(fact["value"] for fact in checker.FACTS.values()),
        "category": "项目档案",
        "tags": ["档案"],
        "keywords": [fact["record_id"] for fact in checker.FACTS.values()],
        "metadata": {
            "analysis_mode": "ai",
            "complete": True,
            "model": "fixture-model",
            "coverage": {
                "total_chunks": len(chunks),
                "processed_chunks": len(chunks),
                "characters": len(sample),
                "chunks": chunks,
            },
            "core_points": [],
            "key_data": [fact["value"] for fact in checker.FACTS.values()],
            "actions": [],
            "evidence": evidence,
        },
    }
    job = {"item_id": "fixture-item"}
    lines = [
        "---",
        "content_id: fixture-item",
        "analysis_mode: ai",
        "analysis_model: fixture-model",
        "analysis_complete: true",
        "source: text",
        "---",
        "",
        "# 来源证据",
        "",
    ]
    for entry in evidence:
        lines.append(
            f"- {entry['claim']}：“{entry['quote']}” "
            f"（{entry['chunk_id']}，字符 {entry['start']}-{entry['end']}）"
        )
    lines.extend(["", "# 原始内容", "", sample, ""])
    return sample, item, job, "\n".join(lines)


def test_verify_item_accepts_complete_grounded_pipeline_fixture():
    sample, item, job, note = make_fixture()

    result = checker.verify_item(item, job, sample, note)

    assert result["passed"] is True
    assert result["grounded_facts"] == {
        "beginning": True,
        "middle": True,
        "tail": True,
    }


def test_verify_item_rejects_missing_actual_tail_value_with_original_preserved():
    sample, item, job, note = make_fixture()
    tail = checker.FACTS["tail"]
    item["summary"] = item["summary"].replace(tail["value"], tail["record_id"])
    item["metadata"]["key_data"].remove(tail["value"])
    item["metadata"]["evidence"] = item["metadata"]["evidence"][:-1]

    with pytest.raises(ValueError, match="missed synthetic facts"):
        checker.verify_item(item, job, sample, note)

    assert note.endswith(sample + "\n")


def test_verify_item_rejects_evidence_with_invalid_source_offsets():
    sample, item, job, note = make_fixture()
    tail_evidence = item["metadata"]["evidence"][-1]
    tail_evidence["start"] += 1
    tail_evidence["end"] += 1

    with pytest.raises(ValueError, match="not grounded/rendered"):
        checker.verify_item(item, job, sample, note)


def test_verify_item_rejects_fact_split_between_claim_and_quote():
    sample, item, job, note = make_fixture()
    tail = checker.FACTS["tail"]
    evidence = item["metadata"]["evidence"][-1]
    start = sample.index(tail["record_id"])
    evidence["quote"] = tail["record_id"]
    evidence["start"] = start
    evidence["end"] = start + len(tail["record_id"])
    note = note.replace(
        "# 原始内容",
        f"{evidence['chunk_id']}，字符 {start}-{evidence['end']}\n\n# 原始内容",
        1,
    )

    with pytest.raises(ValueError, match="lack grounded evidence"):
        checker.verify_item(item, job, sample, note)


@pytest.mark.parametrize(
    ("line", "replacement"),
    [
        ("analysis_complete: true\n", ""),
        ("analysis_complete: true", "analysis_complete: false"),
        ("analysis_model: fixture-model\n", ""),
        ("analysis_model: fixture-model", "analysis_model: another-model"),
    ],
)
def test_verify_item_rejects_missing_or_mismatched_analysis_frontmatter(
    line, replacement
):
    sample, item, job, note = make_fixture()
    note = note.replace(line, replacement, 1)

    with pytest.raises(ValueError, match="frontmatter"):
        checker.verify_item(item, job, sample, note)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "stale-item"),
        ("source_type", "url"),
        ("raw_content", "stale database content"),
    ],
)
def test_verify_item_rejects_stale_or_wrong_database_item(field, value):
    sample, item, job, note = make_fixture()
    item[field] = value

    with pytest.raises(ValueError, match="SQLite item"):
        checker.verify_item(item, job, sample, note)


def test_discover_database_matches_deployed_environment_precedence(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    configured_data = tmp_path / "configured-data"
    environment_data = tmp_path / "environment-data"
    database = environment_data / "custom.sqlite"
    environment_data.mkdir()
    database.touch()
    config.write_text(
        f"data_dir: {configured_data.as_posix()}\ndatabase_path: nested/custom.sqlite\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KNOWLEDGE_CONFIG", str(config))
    monkeypatch.setenv("KNOWLEDGE_DATA_DIR", str(environment_data))

    assert checker.discover_database() == database.resolve()
