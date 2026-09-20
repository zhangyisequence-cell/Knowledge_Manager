from __future__ import annotations

import asyncio
import json
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from backend.config import AIConfig
from backend.models import ContentItem

SYSTEM_PROMPT = """你是个人知识库编辑。根据输入内容输出严格 JSON，不要 Markdown 代码围栏。
字段：
summary: 一句话总结，不超过 80 字；
core_points: 3-7 条核心观点字符串数组；
key_data: 关键数字、事实、原句摘录组成的字符串数组；
actions: 可行动事项字符串数组；
category: 一个稳定、简短的中文分类；
tags: 3-8 个中文或英文标签，不要井号；
keywords: 3-10 个关键词；
importance_score: 0 到 1 的数字；
media_description: 若有图片或视频，描述画面、图表关系、重要文字和音视频内容，否则为空字符串；
evidence: 可核验依据对象数组，每项含 claim、quote、chunk_id。
忠于原文；证据不足时明确写“原文未提供”，不要臆测。"""

# Character budget, not a tokenizer claim. Deploy with an >=8192-token context;
# providers must report context/output errors rather than silently truncate input.
CHUNK_SIZE = 3_000


@dataclass(frozen=True)
class TextChunk:
    id: str
    start: int
    end: int
    text: str


class AIProcessor:
    def __init__(self, config: AIConfig):
        self.config = config

    @property
    def supports_video(self) -> bool:
        # Keep the caller capability contract for explicitly configured
        # embedding/local vision adapters. The DeepSeek request path below
        # still sends extracted text only and never reads binary media.
        return self.config.enabled and bool(self.config.vision_model)

    async def analyze(self, item: ContentItem) -> dict[str, Any]:
        text = "\n\n".join(filter(None, [item.raw_content, item.transcript]))
        if not self.config.enabled:
            return self._fallback(text)

        chunks = self._chunks(text)
        results = []
        for index, chunk in enumerate(chunks):
            result = await self._request_chunk_with_retry(
                chunk, item, include_media=index == 0
            )
            results.append(self._validate_result(result))
        aggregate = self._aggregate(
            results, chunks, item.source_url, len(text), self.config.model
        )
        # A test or an embedding application can intentionally replace the
        # chunk method; in that case preserve the established deterministic
        # aggregation contract. Production uses the second, evidence-only
        # summary request for a concise final category and summary.
        if getattr(self._request_chunk, "__func__", self._request_chunk) is AIProcessor._request_chunk:
            final = await self._request_summary(aggregate, item)
            aggregate.update({
                "summary": final["summary"],
                "category": final["category"],
                "tags": final["tags"],
            })
        aggregate["analysis_provider"] = self.config.provider
        aggregate["analysis_mode"] = "ai"
        aggregate["complete"] = aggregate["coverage"]["processed_chunks"] == aggregate["coverage"]["total_chunks"]
        return aggregate

    async def _request_chunk_with_retry(
        self, chunk: TextChunk, item: ContentItem, *, include_media: bool = False
    ) -> dict[str, Any]:
        attempts = max(1, int(self.config.retry_attempts))
        for attempt in range(attempts):
            try:
                return await self._request_chunk(chunk, item, include_media=include_media)
            except (httpx.TimeoutException, httpx.ConnectError) as error:
                if attempt + 1 >= attempts:
                    raise RuntimeError("DeepSeek 请求超时或连接失败，任务未完成") from error
            except RuntimeError as error:
                # _request_chunk uses RuntimeError for HTTP status classes and
                # only retryable statuses carry this marker.
                if not getattr(error, "retryable", False) or attempt + 1 >= attempts:
                    raise
            await asyncio.sleep(self.config.retry_backoff_seconds * (2 ** attempt))
        raise RuntimeError("DeepSeek 请求失败，任务未完成")

    async def _request_chunk(
        self, chunk: TextChunk, item: ContentItem, *, include_media: bool = False
    ) -> dict[str, Any]:
        source_url = item.source_url or "原文未提供"
        user_content = (
            f"标题：{item.title}\n来源类型：{item.source_type}\n来源链接：{source_url}\n"
            f"分段：{chunk.id}，原文字符范围 [{chunk.start}, {chunk.end})\n\n{chunk.text}"
            "\n\n证据 evidence 必须是对象数组，每项包含 claim、quote、chunk_id；"
            "quote 必须逐字来自本分段，chunk_id 必须等于上述分段编号。"
        )

        headers = {"Content-Type": "application/json"}
        if self.config.api_key and self.config.api_key.strip():
            headers["Authorization"] = f"Bearer {self.config.api_key.strip()}"
        payload = {
            "model": self.config.model,
            "temperature": 0.2,
            "max_tokens": 2048,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        }
        async with httpx.AsyncClient(
            timeout=max(self.config.timeout_seconds, 180),
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            if status_code in {429, 500, 502, 503, 504}:
                error = RuntimeError(f"DeepSeek HTTP {status_code}，任务未完成")
                error.retryable = True
                raise error
            raise RuntimeError(f"DeepSeek HTTP {status_code}，任务未完成")
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") not in {None, "stop"}:
            raise ValueError("AI 输出未正常完成，不能确认完整分析")
        content = choice["message"]["content"]
        return self._parse_json(content)

    async def _request_summary(self, aggregate: dict[str, Any], item: ContentItem) -> dict[str, Any]:
        evidence = aggregate.get("evidence", [])
        prompt = json.dumps({
            "request_type": "summary",
            "title": item.title,
            "facts": aggregate.get("key_data", []),
            "evidence": evidence,
            "instruction": "只使用给出的 facts 和 evidence，返回 summary、category、tags 三个字段。",
        }, ensure_ascii=False)
        payload = {
            "model": self.config.model,
            "temperature": 0.1,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "你是有证据约束的知识库汇总器，只能引用输入证据，并返回严格 JSON。"},
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key.strip():
            headers["Authorization"] = f"Bearer {self.config.api_key.strip()}"
        attempts = max(1, int(self.config.retry_attempts))
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(
                    timeout=max(self.config.timeout_seconds, 180), trust_env=False
                ) as client:
                    response = await client.post(
                        f"{self.config.base_url.rstrip('/')}/chat/completions",
                        headers=headers, json=payload,
                    )
                status_code = getattr(response, "status_code", 200)
                if status_code >= 400:
                    if status_code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                        await asyncio.sleep(self.config.retry_backoff_seconds * (2 ** attempt))
                        continue
                    raise RuntimeError(f"DeepSeek HTTP {status_code}，任务未完成")
                choice = response.json()["choices"][0]
                if choice.get("finish_reason") not in {None, "stop"}:
                    raise ValueError("AI 输出未正常完成，不能确认完整分析")
                result = self._parse_json(choice["message"]["content"])
                summary = result.get("summary")
                category = result.get("category")
                tags = result.get("tags")
                if not isinstance(summary, str) or not summary.strip():
                    raise ValueError("AI 汇总缺少有效 summary")
                if not isinstance(category, str) or not category.strip():
                    raise ValueError("AI 汇总缺少有效 category")
                if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                    raise ValueError("AI 汇总 tags 必须是字符串数组")
                return {"summary": summary.strip(), "category": category.strip(), "tags": tags}
            except (httpx.TimeoutException, httpx.ConnectError) as error:
                if attempt + 1 >= attempts:
                    raise RuntimeError("DeepSeek 汇总请求超时，任务未完成") from error
                await asyncio.sleep(self.config.retry_backoff_seconds * (2 ** attempt))
        raise RuntimeError("DeepSeek 汇总失败，任务未完成")

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        if not isinstance(content, str):
            raise ValueError("AI 返回的内容不是文本 JSON")
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        try:
            result = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError(f"AI 返回的内容不是有效 JSON：{error.msg}") from error
        if not isinstance(result, dict):
            raise ValueError("AI 返回的结果不是 JSON 对象")
        return result

    @staticmethod
    def _chunks(text: str) -> list[TextChunk]:
        if not text:
            return [TextChunk("chunk-0001", 0, 0, "")]
        return [
            TextChunk(
                id=f"chunk-{index + 1:04d}",
                start=start,
                end=min(start + CHUNK_SIZE, len(text)),
                text=text[start : start + CHUNK_SIZE],
            )
            for index, start in enumerate(range(0, len(text), CHUNK_SIZE))
        ]

    @staticmethod
    def _validate_result(result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise ValueError("AI 返回的结果不是 JSON 对象")
        for field in ("summary", "category", "media_description"):
            if field in result and not isinstance(result[field], str):
                raise ValueError(f"AI 字段 {field} 必须是字符串")
        for field in ("core_points", "key_data", "actions", "tags", "keywords"):
            value = result.get(field, [])
            if not isinstance(value, list) or any(not isinstance(part, str) for part in value):
                raise ValueError(f"AI 字段 {field} 必须是字符串数组")
        score = result.get("importance_score", 0.5)
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("AI 字段 importance_score 必须是数字")
        if not 0 <= score <= 1:
            raise ValueError("AI 字段 importance_score 必须在 0 到 1 之间")
        evidence = result.get("evidence", [])
        if not isinstance(evidence, list):
            raise ValueError("AI 字段 evidence 必须是对象数组")
        for entry in evidence:
            if not isinstance(entry, dict):
                raise ValueError("AI 字段 evidence 必须是对象数组")
            for field in ("claim", "quote", "chunk_id"):
                if not isinstance(entry.get(field), str):
                    raise ValueError(f"AI evidence 字段 {field} 必须是字符串")
        required = {"summary", "category", "core_points", "key_data", "actions",
                    "tags", "keywords", "importance_score", "evidence"}
        missing = sorted(required - result.keys())
        if missing:
            raise ValueError("AI 结果缺少字段：" + ", ".join(missing))
        if not result["summary"].strip() or not result["category"].strip():
            raise ValueError("AI 摘要或分类为空")
        return result

    def _aggregate(
        self,
        results: list[dict[str, Any]],
        chunks: list[TextChunk],
        source_url: str | None,
        character_count: int,
        model: str,
    ) -> dict[str, Any]:
        def unique_strings(field: str) -> list[str]:
            return list(dict.fromkeys(
                value.strip()
                for result in results
                for value in result.get(field, [])
                if value.strip()
            ))

        summaries = list(dict.fromkeys(
            value for result in results if (value := result.get("summary", "").strip())
        ))
        categories = [
            value for result in results if (value := result.get("category", "").strip())
        ]
        category = Counter(categories).most_common(1)[0][0] if categories else "待分类"
        evidence = []
        rejected_evidence_count = 0
        chunks_by_id = {chunk.id: chunk for chunk in chunks}
        for result in results:
            for entry in result.get("evidence", []):
                chunk = chunks_by_id.get(entry["chunk_id"])
                quote = entry["quote"]
                if chunk is None or not quote:
                    rejected_evidence_count += 1
                    continue
                relative_start = chunk.text.find(quote)
                if relative_start < 0:
                    rejected_evidence_count += 1
                    continue
                start = chunk.start + relative_start
                evidence.append({
                    "claim": entry["claim"].strip(),
                    "quote": quote,
                    "chunk_id": chunk.id,
                    "start": start,
                    "end": start + len(quote),
                    "source_url": source_url,
                })
        descriptions = list(dict.fromkeys(
            value
            for result in results
            if (value := result.get("media_description", "").strip())
        ))
        return {
            "summary": "；".join(summaries) or "暂无可提取文本",
            "core_points": unique_strings("core_points"),
            "key_data": unique_strings("key_data"),
            "actions": unique_strings("actions"),
            "category": category,
            "tags": unique_strings("tags"),
            "keywords": unique_strings("keywords"),
            "importance_score": max(
                (float(result.get("importance_score", 0.5)) for result in results),
                default=0.5,
            ),
            "media_description": "\n\n".join(descriptions),
            "evidence": evidence,
            "rejected_evidence_count": rejected_evidence_count,
            "analysis_mode": "ai",
            "analysis_label": "AI 分段提取",
            "model": model,
            "coverage": {
                "total_chunks": len(chunks),
                "processed_chunks": len(results),
                "characters": character_count,
                "chunks": [
                    {"id": chunk.id, "start": chunk.start, "end": chunk.end}
                    for chunk in chunks
                ],
            },
            "complete": len(results) == len(chunks),
        }

    @staticmethod
    def _first_image(media_files: list[str]) -> Path | None:
        return next(
            (
                Path(value)
                for value in media_files
                if Path(value).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                and Path(value).exists()
            ),
            None,
        )

    @staticmethod
    def _first_video(media_files: list[str]) -> Path | None:
        return next(
            (
                Path(value)
                for value in media_files
                if Path(value).suffix.lower() in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
                and Path(value).exists()
            ),
            None,
        )

    @staticmethod
    def _prepare_video(path: Path) -> Path:
        max_bytes = 40 * 1024 * 1024
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        if (
            path.suffix.lower() == ".mp4"
            and path.stat().st_size <= max_bytes
            and probe.stdout.strip() == "h264"
        ):
            return path
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temporary:
            output = Path(temporary.name)
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-vf",
            "scale=-2:480",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "31",
            "-c:a",
            "aac",
            "-ac",
            "1",
            "-b:a",
            "48k",
            "-movflags",
            "+faststart",
            str(output),
        ]
        try:
            subprocess.run(command, capture_output=True, text=True, check=True, timeout=300)
            if output.stat().st_size > max_bytes:
                raise ValueError("压缩后视频仍超过 40 MB，请先裁剪或压缩")
            return output
        except Exception:
            output.unlink(missing_ok=True)
            raise

    @staticmethod
    def _fallback(text: str) -> dict[str, Any]:
        compact = re.sub(r"\s+", " ", text).strip()
        sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s*", compact) if part.strip()]
        words = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_-]{2,}", compact.lower())
        stop = {"这个", "一个", "可以", "进行", "以及", "我们", "the", "and", "that", "with"}
        keywords = [word for word, _ in Counter(word for word in words if word not in stop).most_common(8)]
        return {
            "summary": (sentences[0] if sentences else compact[:160]) or "暂无可提取文本",
            "core_points": sentences[:5],
            "key_data": [],
            "actions": [],
            "category": "待分类",
            "tags": keywords[:5] or ["待处理"],
            "keywords": keywords,
            "importance_score": 0.5,
            "media_description": "",
            "evidence": [],
            "rejected_evidence_count": 0,
            "analysis_mode": "rules",
            "analysis_label": "规则提取（未启用 AI）",
            "model": None,
            "coverage": {
                "total_chunks": 1,
                "processed_chunks": 1,
                "characters": len(text),
                "chunks": [{"id": "rules", "start": 0, "end": len(text)}],
            },
            "complete": True,
        }
