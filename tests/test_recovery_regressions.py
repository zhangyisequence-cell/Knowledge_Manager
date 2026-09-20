import asyncio
import sqlite3

import pytest
from backend.adapters import build_registry
from backend.config import AIConfig, AppConfig
from backend.models import Job
from backend.processors.ai import AIProcessor
from backend.processors.linker import KnowledgeLinker
from backend.processors.pipeline import ContentPipeline
from backend.storage import Database, ObsidianWriter


def test_retry_after_note_write_does_not_duplicate_vault_or_database(tmp_path):
    config = AppConfig(data_dir=tmp_path / 'data', vault_dir=tmp_path / 'vault',
                       database_path=tmp_path / 'data' / 'knowledge.sqlite',
                       ai=AIConfig(enabled=False), qmd_command='missing-qmd-test')
    config.prepare()
    database = Database(config.database_path)
    database.initialize()
    original = config.data_dir / 'original.md'
    original.write_text('每周五复盘，预算为一千二百元。', encoding='utf-8')
    job = Job(input_type='file', payload={'path': str(original), 'title': '恢复测试'})
    pipeline = ContentPipeline(build_registry(config), database, ObsidianWriter(config),
                               AIProcessor(config.ai), KnowledgeLinker(config))
    # Reproduce a real database failure after the note and attachment were published.
    with sqlite3.connect(config.database_path) as connection:
        connection.execute("CREATE TRIGGER reject_item BEFORE INSERT ON items "
                           "BEGIN SELECT RAISE(ABORT, 'synthetic outage'); END")
    with pytest.raises(sqlite3.IntegrityError, match='synthetic outage'):
        asyncio.run(pipeline.process(job))
    first_notes = list((config.vault_dir / config.inbox_folder).glob('*.md'))
    assert len(first_notes) == 1
    with sqlite3.connect(config.database_path) as connection:
        connection.execute('DROP TRIGGER reject_item')
    item, note = asyncio.run(pipeline.process(job))
    assert note == first_notes[0]
    assert item.id == job.id
    assert list((config.vault_dir / config.inbox_folder).glob('*.md')) == first_notes
    attachments = list((config.vault_dir / config.inbox_folder / '_attachments').rglob('*.md'))
    assert len(attachments) == 1 and attachments[0].read_bytes() == original.read_bytes()
    assert len(database.list_items()) == 1
