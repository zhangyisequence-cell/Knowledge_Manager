"""Real routing, pipeline and vault writes; model calls never download or contact a service."""

import asyncio
from types import SimpleNamespace

import pytest
from backend.adapters import build_registry
from backend.config import AIConfig, AppConfig
from backend.models import Job
from backend.processors.ai import AIProcessor
from backend.processors.linker import KnowledgeLinker
from backend.processors.pipeline import ContentPipeline
from backend.processors.transcriber import Transcriber
from backend.storage.db import Database
from backend.storage.obsidian import ObsidianWriter


def make_pipeline(tmp_path, vision=False):
    config = AppConfig(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "data" / "jobs.sqlite",
        vault_dir=tmp_path / "vault",
        ai=AIConfig(enabled=vision, vision_model="test-vision" if vision else None),
        qmd_command="nonexistent-qmd-for-media-validation",
    )
    config.prepare()
    database = Database(config.database_path)
    database.initialize()
    return ContentPipeline(
        build_registry(config), database, ObsidianWriter(config),
        AIProcessor(config.ai), KnowledgeLinker(config),
    ), config


def media_job(tmp_path, suffix, content=b"synthetic media fixture"):
    path = tmp_path / f"sample{suffix}"
    path.write_bytes(content)
    return Job(input_type="file", payload={"path": str(path)}), path


async def simulated_vision(self, item):
    return {**self._fallback("测试视频中的资料归档。"), "media_description": "测试视频画面。"}


@pytest.mark.parametrize("suffix", [".mp3", ".wav", ".m4a", ".amr", ".aac", ".flac", ".ogg", ".opus"])
def test_vision_model_cannot_turn_untranscribed_audio_into_success(tmp_path, monkeypatch, suffix):
    pipeline, config = make_pipeline(tmp_path, vision=True)
    monkeypatch.setattr(Transcriber, "_model", staticmethod(lambda: None))
    monkeypatch.setattr(AIProcessor, "analyze", simulated_vision)
    job, path = media_job(tmp_path, suffix)

    with pytest.raises(RuntimeError, match="未生成转写文本"):
        asyncio.run(pipeline.process(job))

    assert path.is_file()
    assert not list(config.vault_dir.rglob("*.md"))


@pytest.mark.parametrize("suffix,vision,accepted", [
    (".mp4", True, True), (".mkv", True, True),
    (".mp4", False, False), (".avi", True, False),
])
def test_only_supported_video_can_use_vision_without_transcript(
    tmp_path, monkeypatch, suffix, vision, accepted,
):
    pipeline, config = make_pipeline(tmp_path, vision=vision)
    monkeypatch.setattr(Transcriber, "_model", staticmethod(lambda: None))
    monkeypatch.setattr(AIProcessor, "analyze", simulated_vision)
    job, path = media_job(tmp_path, suffix)
    if not accepted:
        with pytest.raises(RuntimeError, match="未生成转写文本"):
            asyncio.run(pipeline.process(job))
        assert not list(config.vault_dir.rglob("*.md"))
        return

    item, note = asyncio.run(pipeline.process(job))
    assert item.metadata["media_description"] == "测试视频画面。"
    assert "测试视频画面。" in note.read_text("utf-8")
    assert next(config.vault_dir.rglob(f"*{suffix}")).read_bytes() == path.read_bytes()


@pytest.mark.parametrize("suffix", [
    ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus", ".amr",
    ".aif", ".aiff", ".wma", ".m4b", ".oga", ".AMR",
])
def test_common_audio_upload_reaches_transcriber_and_preserves_original(
    tmp_path, monkeypatch, suffix,
):
    pipeline, config = make_pipeline(tmp_path)
    job, path = media_job(tmp_path, suffix)

    class TranscriptModel:
        def transcribe(self, received, **kwargs):
            assert received == str(path)
            return iter([SimpleNamespace(text="预算12800元，请李明周五完成复盘。")]), None

    monkeypatch.setattr(Transcriber, "_model", staticmethod(TranscriptModel))
    item, note = asyncio.run(pipeline.process(job))
    assert item.transcript == "预算12800元，请李明周五完成复盘。"
    assert item.transcript in note.read_text("utf-8")
    assert next(config.vault_dir.rglob(f"*{suffix}")).read_bytes() == path.read_bytes()


@pytest.mark.parametrize("suffix,header", [
    (".silk", b"#!SILK_V3"), (".amr", b"\x02#!SILK_V3"), (".mp3", b"#!SILK_V3"),
])
def test_silk_is_explicitly_rejected_even_with_a_supported_filename(
    tmp_path, monkeypatch, suffix, header,
):
    pipeline, config = make_pipeline(tmp_path, vision=True)
    monkeypatch.setattr(Transcriber, "_model", staticmethod(lambda: None))
    monkeypatch.setattr(AIProcessor, "analyze", simulated_vision)
    job, _ = media_job(tmp_path, suffix, header + b"unsupported codec")
    with pytest.raises(ValueError, match="SILK.*不支持"):
        asyncio.run(pipeline.process(job))
    assert not list(config.vault_dir.rglob("*.md"))
