import importlib.util
import sys
from pathlib import Path

from backend.config import AppConfig
from backend.models import ContentItem
from backend.storage.db import Database
from backend.storage.obsidian import ObsidianWriter

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "check_media_ingestion", SCRIPTS / "check_media_ingestion.py"
)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def fixture(tmp_path, *, model=True, changed_database_transcript=False):
    config = AppConfig(
        data_dir=tmp_path / "data",
        database_path=tmp_path / "data" / "knowledge.sqlite",
        vault_dir=tmp_path / "vault",
    )
    config.prepare()
    database = Database(config.database_path)
    database.initialize()
    source = tmp_path / "chinese-validation.wav"
    source.write_bytes(b"synthetic-wave")
    transcript = "预算12800元，负责人李明，团队周五复盘，下周三完成归档。"
    metadata = {
        "analysis_mode": "rules",
        "complete": True,
        "transcription_review_required": True,
        "transcription_engine": "faster-whisper",
        "transcription_model_reference": "/models/snapshot",
        "transcription_model_revision": checker.PINNED_REVISION,
        "transcription_decoding": {
            "vad_filter": True,
            "beam_size": 5,
            "language": "auto",
        },
        "transcription_runtime": {"device": "cpu", "compute_type": "int8"},
    }
    if model:
        metadata["transcription_model"] = checker.EXPECTED_MODEL
    item = ContentItem(
        id="media-fixture", source_type="local_file", title="媒体验收",
        media_files=[str(source)], transcript=transcript, metadata=metadata,
    )
    note = ObsidianWriter(config).write(item)
    if changed_database_transcript:
        item.transcript = "数据库中的转写已被改变。"
    database.save_item(item, str(note))
    job = {
        "status": "succeeded",
        "item_id": item.id,
        "note_path": str(note),
        "payload": {"path": str(source)},
    }
    return job, source, config.database_path


def test_verify_job_accepts_writer_note_and_matching_database(tmp_path):
    job, source, database = fixture(tmp_path)

    result = checker.verify_job(job, source, tmp_path, database)

    assert result["pipeline_passed"] is True
    assert result["transcription_accuracy_passed"] is True


def test_verify_job_rejects_missing_expected_model(tmp_path):
    job, source, database = fixture(tmp_path, model=False)

    result = checker.verify_job(job, source, tmp_path, database)

    assert result["pipeline_checks"]["expected_model_revision_pinned"] is False
    assert result["pipeline_passed"] is False


def test_verify_job_rejects_changed_database_transcript(tmp_path):
    job, source, database = fixture(tmp_path, changed_database_transcript=True)

    result = checker.verify_job(job, source, tmp_path, database)

    assert result["pipeline_checks"]["sqlite_transcript_matches_note"] is False
    assert result["pipeline_passed"] is False
