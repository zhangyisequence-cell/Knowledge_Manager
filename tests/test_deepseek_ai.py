from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from backend.config import AIConfig
from backend.models import ContentItem
from backend.processors.ai import AIProcessor
from backend.processors.pipeline import ContentPipeline


def _result(chunk_id: str, quote: str = "正文") -> dict:
    return {
        "summary": "摘要",
        "core_points": ["观点"],
        "key_data": ["事实"],
        "actions": [],
        "category": "测试",
        "tags": ["标签"],
        "keywords": ["关键词"],
        "importance_score": 0.5,
        "media_description": "",
        "evidence": [{"claim": "事实", "quote": quote, "chunk_id": chunk_id}],
    }


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def test_deepseek_posts_json_text_only_and_reports_provider(monkeypatch):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append((request, body))
        if body.get("messages", [{}, {}])[1].get("content", "").lstrip().startswith("{"):
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
                "summary": "最终摘要", "category": "测试", "tags": ["标签"],
            })}, "finish_reason": "stop"}]})
        chunk_id = body["messages"][1]["content"].split("分段：", 1)[1].split("，", 1)[0]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            _result(chunk_id, "正文")
        )}, "finish_reason": "stop"}], "usage": {"total_tokens": 12}})

    _install_transport(monkeypatch, handler)
    processor = AIProcessor(AIConfig(
        enabled=True,
        base_url="https://api.deepseek.com",
        api_key="secret-token",
        model="deepseek-chat",
        timeout_seconds=1,
        retry_attempts=1,
    ))
    item = ContentItem(
        source_type="video",
        title="标题",
        raw_content="正文",
        media_files=[str(Path("private-video.mp4"))],
    )
    result = asyncio.run(processor.analyze(item))

    assert result["analysis_provider"] == "deepseek"
    assert result["model"] == "deepseek-chat"
    assert result["complete"] is True
    assert result["coverage"]["processed_chunks"] == result["coverage"]["total_chunks"]
    assert requests[0][0].url == "https://api.deepseek.com/chat/completions"
    assert requests[0][0].headers["authorization"] == "Bearer secret-token"
    assert requests[0][1]["model"] == "deepseek-chat"
    assert requests[0][1]["response_format"] == {"type": "json_object"}
    assert "metadata" not in requests[-1][1]
    assert "JSON" in requests[-1][1]["messages"][0]["content"]
    encoded = json.dumps(requests[0][1], ensure_ascii=False)
    assert "private-video.mp4" not in encoded
    assert "secret-token" not in encoded
    assert "正文" in encoded


def test_pipeline_persists_analysis_provider_metadata():
    item = ContentItem(source_type="text", raw_content="正文")
    ContentPipeline._apply_analysis(item, {
        "summary": "摘要",
        "category": "测试",
        "tags": ["标签"],
        "keywords": ["关键词"],
        "importance_score": 0.5,
        "core_points": [],
        "key_data": [],
        "actions": [],
        "evidence": [],
        "analysis_provider": "deepseek",
        "analysis_mode": "ai",
        "analysis_label": "AI 分段提取",
        "model": "deepseek-chat",
        "coverage": {"total_chunks": 1, "processed_chunks": 1},
        "complete": True,
    })
    assert item.metadata["analysis_provider"] == "deepseek"


@pytest.mark.parametrize("status", [401, 400, 402, 429, 500])
def test_deepseek_http_errors_are_redacted_and_classified(monkeypatch, status):
    async def handler(request):
        return httpx.Response(status, json={"error": {"message": "secret response"}})

    _install_transport(monkeypatch, handler)
    processor = AIProcessor(AIConfig(
        enabled=True, api_key="do-not-leak", retry_attempts=1, timeout_seconds=1
    ))
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(processor.analyze(ContentItem(source_type="text", raw_content="正文")))
    assert "do-not-leak" not in str(exc.value)
    assert "secret response" not in str(exc.value)
    assert str(status) in str(exc.value)


def test_deepseek_rejects_truncated_or_invalid_evidence(monkeypatch):
    async def handler(request):
        chunk_id = "chunk-0001"
        content = _result(chunk_id, "不存在")
        return httpx.Response(200, json={"choices": [{
            "message": {"content": json.dumps(content)}, "finish_reason": "length"
        }]})

    _install_transport(monkeypatch, handler)
    processor = AIProcessor(AIConfig(enabled=True, retry_attempts=1, timeout_seconds=1))
    with pytest.raises(ValueError):
        asyncio.run(processor.analyze(ContentItem(source_type="text", raw_content="正文")))


def test_retry_failure_does_not_mark_partial_result_complete(monkeypatch):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 2:
            return httpx.Response(503)
        chunk_id = request.content.decode("utf-8").split("chunk-", 1)[1][:4]
        return httpx.Response(200, json={"choices": [{
            "message": {"content": json.dumps(_result(f"chunk-{chunk_id}"))},
            "finish_reason": "stop",
        }]})

    _install_transport(monkeypatch, handler)
    processor = AIProcessor(AIConfig(enabled=True, retry_attempts=1, timeout_seconds=1))
    with pytest.raises(RuntimeError, match="503"):
        asyncio.run(processor.analyze(ContentItem(source_type="text", raw_content="A" * 6000)))
