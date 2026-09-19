"""Real routing, pipeline and vault writes; model calls never download or contact a service."""

import asyncio
from types import SimpleNamespace

import pytest
import yaml
from backend.adapters import build_registry
from backend.config import AIConfig, AppConfig
from backend.models import ContentItem, Job
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
    ".aif", ".aiff", ".wma", ".m4b", ".oga", ".AMR", ".mp4",
])
def test_common_audio_upload_reaches_transcriber_and_preserves_original(
    tmp_path, monkeypatch, suffix,
):
    pipeline, config = make_pipeline(tmp_path)
    job, path = media_job(tmp_path, suffix)

    class TranscriptModel:
        def transcribe(self, received, **kwargs):
            assert received == str(path)
            assert kwargs == {"vad_filter": True, "beam_size": 5}
            return iter([SimpleNamespace(text="预算12800元，请李明周五完成复盘。")]), None

    monkeypatch.setattr(Transcriber, "_model", staticmethod(TranscriptModel))
    item, note = asyncio.run(pipeline.process(job))
    assert item.transcript == "预算12800元，请李明周五完成复盘。"
    assert item.transcript in note.read_text("utf-8")
    assert item.metadata["transcription_review_required"] is True
    note_text = note.read_text("utf-8")
    frontmatter = yaml.safe_load(note_text.split("---", 2)[1])
    assert item.source_type == "local_file"
    assert frontmatter["source"] == "local_file"
    assert pipeline.database.list_items()[0]["source_type"] == "local_file"
    assert frontmatter["transcription_review_required"] is True
    assert frontmatter["transcription_engine"] == "faster-whisper"
    assert "## 转写文本\n\n> 自动转写，未经人工复核" in note_text
    assert next(config.vault_dir.rglob(f"*{suffix}")).read_bytes() == path.read_bytes()


@pytest.mark.parametrize("suffix", [".wav", ".mp4", ".amr"])
def test_wechat_file_origin_reaches_media_note(tmp_path, monkeypatch, suffix):
    pipeline, _ = make_pipeline(tmp_path)
    job, _ = media_job(tmp_path, suffix)
    job.payload["source_type"] = "wechat"

    class Model:
        def transcribe(self, received, **kwargs):
            return iter([SimpleNamespace(text="保持原始转写。")]), None

    monkeypatch.setattr(Transcriber, "_model", staticmethod(Model))
    item, note = asyncio.run(pipeline.process(job))
    assert item.source_type == "wechat"
    assert yaml.safe_load(note.read_text("utf-8").split("---", 2)[1])["source"] == "wechat"
    assert pipeline.database.list_items()[0]["source_type"] == "wechat"


def test_pinned_model_provenance_does_not_claim_confidence(tmp_path, monkeypatch):
    pipeline, _ = make_pipeline(tmp_path)
    job, _ = media_job(tmp_path, ".wav")
    revision = "536b0662742c02347bc0e980a01041f333bce120"
    snapshot = tmp_path / "models--Systran--faster-whisper-small" / "snapshots" / revision
    monkeypatch.setenv("KNOWLEDGE_WHISPER_MODEL", str(snapshot))

    class Model:
        def transcribe(self, received, **kwargs):
            return iter([SimpleNamespace(text="请完成归当。")]), None

    monkeypatch.setattr(Transcriber, "_model", staticmethod(Model))
    item, note = asyncio.run(pipeline.process(job))
    assert item.transcript == "请完成归当。"  # No homophone correction or expected-answer hints.
    assert item.metadata["transcription_model"] == "Systran/faster-whisper-small"
    assert item.metadata["transcription_model_revision"] == revision
    assert item.metadata["transcription_decoding"] == {
        "vad_filter": True, "beam_size": 5, "language": "auto",
    }
    assert item.metadata["transcription_runtime"] == {"device": "cpu", "compute_type": "int8"}
    assert "confidence" not in str(item.metadata)
    text = note.read_text("utf-8")
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["transcription_model_revision"] == revision
    assert frontmatter["transcription_decoding"] == item.metadata["transcription_decoding"]
    assert frontmatter["analysis_mode"] == "rules"
    assert "自动转写，未经人工复核" in text


@pytest.mark.parametrize("supplied_transcript", [False, True])
def test_supplied_text_and_transcript_are_not_marked_as_generated(
    tmp_path, monkeypatch, supplied_transcript,
):
    pipeline, _ = make_pipeline(tmp_path)
    if supplied_transcript:
        job, path = media_job(tmp_path, ".wav")

        async def provided(_job):
            return ContentItem(source_type="podcast", transcript="人工核对的转写正文。",
                               media_files=[str(path)])

        monkeypatch.setattr(pipeline, "_extract", provided)
    else:
        job = Job(input_type="text", payload={"text": "用户自己输入的原文。"})

    def no_model():
        raise AssertionError("Existing text/transcripts must not be transcribed")

    monkeypatch.setattr(Transcriber, "_model", staticmethod(no_model))
    item, note = asyncio.run(pipeline.process(job))
    assert "transcription_review_required" not in item.metadata
    assert "自动转写，未经人工复核" not in note.read_text("utf-8")


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
