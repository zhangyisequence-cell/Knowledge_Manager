from __future__ import annotations

import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote

import yaml

from backend.adapters.utils import safe_filename
from backend.config import AppConfig
from backend.models import ContentItem


class ObsidianWriter:
    def __init__(self, config: AppConfig):
        self.config = config

    def write(self, item: ContentItem) -> Path:
        if not self.config.vault_dir:
            raise RuntimeError("请先配置知识库文件夹")
        folder = self.config.vault_dir / self.config.inbox_folder
        folder.mkdir(parents=True, exist_ok=True)
        date = item.created_at.astimezone().strftime("%Y-%m-%d")
        filename = f"{date}-{safe_filename(item.title, item.id)}-{item.id[:8]}.md"
        target = folder / filename
        # Keep originals inside the vault so a synced vault remains self-contained.
        localized = []
        replacements = {}
        for index, raw_path in enumerate(item.media_files):
            source = Path(raw_path).resolve()
            if not source.is_file():
                raise FileNotFoundError(f"原始附件不存在：{source.name}")
            asset_dir = folder / "_attachments" / item.id
            asset_dir.mkdir(parents=True, exist_ok=True)
            asset = asset_dir / f"{index:03d}-{safe_filename(source.stem)}{source.suffix}"
            with NamedTemporaryFile(dir=asset_dir, delete=False) as temporary:
                temporary_path = Path(temporary.name)
            try:
                shutil.copyfile(source, temporary_path)
                os.replace(temporary_path, asset)
            finally:
                temporary_path.unlink(missing_ok=True)
            href = quote(asset.relative_to(folder).as_posix(), safe="/")
            localized.append(href)
            replacements[source.as_uri()] = href
        content = self.format(item, attachment_links=localized)
        for original, href in replacements.items():
            content = content.replace(original, href)
        with NamedTemporaryFile("w", encoding="utf-8", dir=folder, delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, target)
        return target

    @staticmethod
    def format(item: ContentItem, attachment_links: list[str] | None = None) -> str:
        frontmatter = {
            "title": item.title,
            "source": item.source_type,
            "source_url": item.source_url,
            "author": item.author,
            "created": (item.created_time or item.created_at).isoformat(),
            "category": item.category,
            "tags": item.tags,
            "importance": item.importance_score,
            "content_id": item.id,
            "analysis_mode": item.metadata.get("analysis_mode"),
            "analysis_provider": item.metadata.get("analysis_provider"),
            "analysis_model": item.metadata.get("model"),
            "analysis_complete": item.metadata.get("complete"),
        }
        generated_transcript = (
            bool(item.transcript) and item.metadata.get("transcription_review_required") is True
        )
        if generated_transcript:
            for key in (
                "transcription_review_required", "transcription_engine", "transcription_model",
                "transcription_model_reference", "transcription_model_revision",
                "transcription_decoding", "transcription_runtime",
            ):
                if key in item.metadata:
                    frontmatter[key] = item.metadata[key]
        yaml_text = yaml.safe_dump(
            frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=False
        ).strip()
        core_points = ObsidianWriter._numbered(item.metadata.get("core_points", []))
        key_data = ObsidianWriter._bullets(item.metadata.get("key_data", []))
        actions = ObsidianWriter._bullets(item.metadata.get("actions", []))
        related = ObsidianWriter._bullets([f"[[{note}]]" for note in item.related_notes])
        analysis_label = item.metadata.get("analysis_label") or "未标记"
        evidence = ObsidianWriter._evidence(item.metadata.get("evidence", []))
        rejected_evidence_count = item.metadata.get("rejected_evidence_count", 0)
        evidence_notice = (
            f"\n\n> 已剔除 {rejected_evidence_count} 条无法在原文逐字核验的模型引用。"
            if rejected_evidence_count
            else ""
        )
        transcript_notice = "> 自动转写，未经人工复核\n\n" if generated_transcript else ""
        transcript = (
            f"\n\n## 转写文本\n\n{transcript_notice}{item.transcript}" if item.transcript else ""
        )
        description = (
            f"\n\n## 媒体理解\n\n{item.metadata['media_description']}"
            if item.metadata.get("media_description")
            else ""
        )
        attachments = (
            "\n\n## 原始附件\n\n" + "\n".join(
                f"- [附件 {index + 1}]({href})" for index, href in enumerate(attachment_links)
            )
            if attachment_links
            else "" if attachment_links is not None
            else ObsidianWriter._attachments(item.media_files)
        )
        return f"""---
{yaml_text}
---

# 一句话总结

{item.summary or "暂无"}

> 分析方式：{analysis_label}

# 核心观点

{core_points or "1. 暂无"}

# 关键数据

{key_data or "- 原文未提供"}

# 来源证据

{evidence or "- 未提供可核验原文引用"}{evidence_notice}

# 我的关联

结合我的已有知识：

{related or "- 暂未发现可靠关联"}

# 可行动事项

{actions or "- 暂无"}

# 原始内容

{item.raw_content or "（无正文，参见转写或附件）"}{transcript}{description}{attachments}
"""

    @staticmethod
    def _numbered(values: list[str]) -> str:
        return "\n".join(f"{index}. {value}" for index, value in enumerate(values, 1))

    @staticmethod
    def _bullets(values: list[str]) -> str:
        return "\n".join(f"- {value}" for value in values)

    @staticmethod
    def _attachments(paths: list[str]) -> str:
        lines = []
        for raw_path in paths:
            path = Path(raw_path)
            try:
                href = path.resolve().as_uri()
            except ValueError:
                continue
            lines.append(f"- [{path.name}]({href})")
        return f"\n\n## 原始附件\n\n{chr(10).join(lines)}" if lines else ""

    @staticmethod
    def _evidence(values: object) -> str:
        if not isinstance(values, list):
            return ""
        lines = []
        for value in values:
            if not isinstance(value, dict):
                continue
            claim = str(value.get("claim") or "证据")
            quote_text = str(value.get("quote") or "")
            chunk_id = str(value.get("chunk_id") or "")
            start = value.get("start")
            end = value.get("end")
            if not quote_text or not isinstance(start, int) or not isinstance(end, int):
                continue
            location = f"{chunk_id}，字符 {start}-{end}"
            source_url = value.get("source_url")
            if source_url:
                location += f"，[来源]({source_url})"
            lines.append(f"- {claim}：“{quote_text}” （{location}）")
        return "\n".join(lines)
