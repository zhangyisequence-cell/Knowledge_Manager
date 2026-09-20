from __future__ import annotations

import asyncio

import pytest

from backend.config import AIConfig
from backend.models import ContentItem
from backend.processors.ai import AIProcessor
from backend.processors.pipeline import ContentPipeline
from backend.storage.obsidian import ObsidianWriter


def test_long_text_sends_every_character_and_aggregates_tail(monkeypatch) -> None:
    text = "A" * 80_000 + "TAIL_SENTINEL"
    processor = AIProcessor(AIConfig(enabled=True, model="test-model"))
    seen = []

    async def fake_request(chunk, item, *, include_media=False):
        seen.append(chunk)
        quote = "TAIL_SENTINEL" if "TAIL_SENTINEL" in chunk.text else chunk.text[:8]
        return {
            "summary": f"summary-{chunk.id}",
            "core_points": [f"point-{chunk.id}"],
            "key_data": [f"data-{chunk.id}"],
            "actions": [f"action-{chunk.id}"],
            "category": "测试",
            "tags": ["标签"],
            "keywords": ["关键词"],
            "importance_score": 0.5,
            "media_description": "",
            "evidence": [{"claim": "covered", "quote": quote, "chunk_id": chunk.id}],
        }

    monkeypatch.setattr(processor, "_request_chunk", fake_request)
    result = asyncio.run(processor.analyze(ContentItem(source_type="text", raw_content=text)))

    assert "".join(chunk.text for chunk in seen) == text
    assert [(chunk.start, chunk.end) for chunk in seen] == [
        (0, seen[0].end),
        *[(seen[index - 1].end, chunk.end) for index, chunk in enumerate(seen[1:], 1)],
    ]
    assert any(item["quote"] == "TAIL_SENTINEL" for item in result["evidence"])
    assert result["coverage"] == {
        "total_chunks": len(seen),
        "processed_chunks": len(seen),
        "characters": len(text),
        "chunks": [
            {"id": chunk.id, "start": chunk.start, "end": chunk.end}
            for chunk in seen
        ],
    }
    assert result["complete"] is True
    assert result["analysis_mode"] == "ai"


def test_invalid_json_fails_instead_of_rule_fallback() -> None:
    with pytest.raises(ValueError, match="不是有效 JSON"):
        AIProcessor._parse_json("not json")


def test_empty_json_cannot_be_reported_as_complete_analysis() -> None:
    with pytest.raises(ValueError, match="缺少"):
        AIProcessor._validate_result({})


def test_chunks_fit_a_small_local_model_input_budget() -> None:
    chunks = AIProcessor._chunks('中文内容' * 3000)
    assert len(chunks) >= 4
    assert all(len(chunk.text) <= 3000 for chunk in chunks)


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"summary": ["wrong"]}, "summary"),
        ({"tags": "wrong"}, "tags"),
        ({"importance_score": "high"}, "importance_score"),
        ({"evidence": [{"claim": "x", "quote": 3, "chunk_id": "chunk-0001"}]}, "quote"),
    ],
)
def test_invalid_ai_field_types_fail(payload, message) -> None:
    with pytest.raises(ValueError, match=message):
        AIProcessor._validate_result(payload)


def test_rule_fallback_is_stored_and_visible() -> None:
    processor = AIProcessor(AIConfig(enabled=False))
    analysis = asyncio.run(
        processor.analyze(ContentItem(source_type="text", raw_content="第一句。第二句。"))
    )
    item = ContentItem(source_type="text", raw_content="第一句。第二句。")
    ContentPipeline._apply_analysis(item, analysis)
    note = ObsidianWriter.format(item)

    assert item.metadata["analysis_mode"] == "rules"
    assert item.metadata["analysis_label"] == "规则提取（未启用 AI）"
    assert "规则提取（未启用 AI）" in note


def test_grounded_evidence_keeps_offsets_and_source_url(monkeypatch) -> None:
    text = "甲事实。乙事实。"
    source_url = "https://example.com/source"
    processor = AIProcessor(AIConfig(enabled=True, model="test-model"))

    async def fake_request(chunk, item, *, include_media=False):
        assert item.source_url == source_url
        return {
            "summary": "摘要",
            "core_points": [],
            "key_data": [],
            "actions": [],
            "category": "测试",
            "tags": [],
            "keywords": [],
            "importance_score": 0.5,
            "media_description": "",
            "evidence": [
                {"claim": "真实", "quote": "乙事实", "chunk_id": chunk.id},
                {"claim": "幻觉", "quote": "原文不存在", "chunk_id": chunk.id},
            ],
        }

    monkeypatch.setattr(processor, "_request_chunk", fake_request)
    analysis = asyncio.run(
        processor.analyze(
            ContentItem(source_type="url", source_url=source_url, raw_content=text)
        )
    )
    assert analysis["evidence"] == [
        {
            "claim": "真实",
            "quote": "乙事实",
            "chunk_id": "chunk-0001",
            "start": text.index("乙事实"),
            "end": text.index("乙事实") + len("乙事实"),
            "source_url": source_url,
        }
    ]
    assert analysis["rejected_evidence_count"] == 1

    item = ContentItem(source_type="url", source_url=source_url, raw_content=text)
    ContentPipeline._apply_analysis(item, analysis)
    note = ObsidianWriter.format(item)
    assert "乙事实" in note
    assert f"字符 {text.index('乙事实')}-{text.index('乙事实') + len('乙事实')}" in note
    assert source_url in note
    assert "已剔除 1 条" in note


def test_source_url_is_sent_in_each_ai_prompt(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {"message": {"content": '{"summary":"摘要"}'}}
                ]
            }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr("backend.processors.ai.httpx.AsyncClient", FakeClient)
    processor = AIProcessor(AIConfig(enabled=True, model="test-model"))
    item = ContentItem(
        source_type="url",
        source_url="https://example.com/original",
        raw_content="正文",
    )
    chunk = processor._chunks(item.raw_content)[0]
    asyncio.run(processor._request_chunk(chunk, item))

    prompt = captured["json"]["messages"][1]["content"]
    assert "来源链接：https://example.com/original" in prompt
    assert "分段：chunk-0001" in prompt
