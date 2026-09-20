"""WeChat cursors must never outrun persisted messages or duplicate ingestion."""
import sqlite3

import pytest
from backend.models import JobStatus
from backend.storage import Database


def store_at(tmp_path):
    from backend.wechat.store import WeChatStore

    store = WeChatStore(tmp_path / 'knowledge.sqlite')
    store.initialize()
    return store


def message(msgid='m1', sender='alice', origin=3):
    return {'msgid': msgid, 'external_userid': sender, 'open_kfid': 'kf1',
            'origin': origin, 'send_time': 1800000000, 'msgtype': 'text',
            'text': {'content': '灵感：每周五复盘'}}


def test_received_page_and_cursor_survive_reopen_and_duplicate_delivery(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'sync-token')
    store.save_page('kf1', '', 'cursor1', [message()], {'alice'})
    store = store_at(tmp_path)
    assert store.account('kf1')['cursor'] == 'cursor1'
    assert store.pending_messages()[0]['payload']['text']['content'] == '灵感：每周五复盘'
    store.save_page('kf1', 'cursor1', 'cursor2', [message()], {'alice'})
    assert len(store.pending_messages()) == 1


def test_invalid_page_rolls_back_all_messages_and_cursor(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'token')
    with pytest.raises(ValueError):
        store.save_page('kf1', '', 'lost-cursor', [message(), {'msgtype': 'text'}], {'alice'})
    assert store.account('kf1')['cursor'] == ''
    assert store.pending_messages() == []


def test_only_authorized_incoming_messages_enter_pending_queue(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'token')
    store.save_page('kf1', '', 'cursor', [message(), message('m2', 'mallory'),
                                        message('m3', origin=5)], {'alice'})
    assert [row['msgid'] for row in store.pending_messages()] == ['m1']


def test_new_notification_during_sync_is_not_lost(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'token1')
    generation = store.account('kf1')['generation']
    store.notify('kf1', 'token2')
    store.finish_sync('kf1', generation, '')
    assert store.accounts_to_sync()[0]['token'] == 'token2'
    current = store.account('kf1')['generation']
    store.finish_sync('kf1', current, '')
    assert store.accounts_to_sync() == []


def test_enqueue_is_atomic_and_does_not_reset_finished_job_on_retry(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'token')
    store.save_page('kf1', '', 'cursor', [message()], {'alice'})
    job, created = store.enqueue('kf1', 'm1', 'text', {'text': '灵感：每周五复盘'})
    assert created and job.status == JobStatus.QUEUED
    database = Database(store.path)
    database.update_job(job.id, JobStatus.SUCCEEDED, item_id='item', note_path='note.md')
    retried, created = store.enqueue('kf1', 'm1', 'text', {'text': 'unexpected duplicate'})
    assert not created
    assert retried.id == job.id and retried.status == JobStatus.SUCCEEDED
    assert retried.payload['text'] == '灵感：每周五复盘'
    assert store.pending_messages() == []
    assert len(database.list_jobs()) == 1


def test_stale_cursor_or_mismatched_account_cannot_skip_messages(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'token')
    store.save_page('kf1', '', 'cursor1', [message()], {'alice'})
    with pytest.raises(ValueError):
        store.save_page('kf1', '', 'cursor2', [], {'alice'})
    with pytest.raises(ValueError):
        store.save_page('kf1', 'cursor1', 'cursor2', [{**message('m2'), 'open_kfid': 'other'}], {'alice'})
    assert store.account('kf1')['cursor'] == 'cursor1'


def test_reply_is_durable_unique_and_utf8_bounded(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'token')
    store.save_page('kf1', '', 'cursor', [message()], {'alice'})
    reply = store.queue_reply('kf1', 'm1', '总结：' + '中文' * 700)
    assert len(reply['content'].encode()) <= 2048
    assert len(reply['send_id']) <= 32
    duplicate = store_at(tmp_path).queue_reply('kf1', 'm1', 'duplicate')
    assert duplicate['send_id'] == reply['send_id']
    assert duplicate['content'] == reply['content']
    assert store.outbox()[0]['status'] == 'pending'
    store.mark_reply(reply['send_id'], 'accepted')
    assert store.outbox() == []
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('SELECT status FROM wechat_outbox').fetchone()[0] == 'accepted'


def test_ignored_sender_cannot_receive_a_reply(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'token')
    store.save_page('kf1', '', 'cursor', [message(sender='mallory')], {'alice'})
    with pytest.raises(ValueError):
        store.queue_reply('kf1', 'm1', 'private result')


def test_results_only_become_reply_candidates_after_job_finishes(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'token')
    store.save_page('kf1', '', 'cursor', [message()], {'alice'})
    job, _ = store.enqueue('kf1', 'm1', 'text', {'text': 'original'})
    assert store.results_to_reply() == []
    Database(store.path).update_job(job.id, JobStatus.FAILED, error='OCR unavailable')
    assert store.results_to_reply()[0]['job_error'] == 'OCR unavailable'
    store.queue_reply('kf1', 'm1', '未成功：OCR unavailable')
    assert store.results_to_reply() == []


def test_latest_authorized_message_renews_reply_window_and_notification_wakes_retry(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'token')
    store.retry_sync('kf1', 'network failed', delay=600)
    assert store.accounts_to_sync() == []
    store.notify('kf1', 'new-token')
    assert store.accounts_to_sync()[0]['token'] == 'new-token'
    store.save_page('kf1', '', 'c1', [message()], {'alice'})
    assert store.reply_deadline('kf1', 'alice') == 1800000000 + 48 * 3600
    store.save_page('kf1', 'c1', 'c2', [{**message('m2'), 'send_time': 1800000500}], {'alice'})
    assert store.reply_deadline('kf1', 'alice') == 1800000500 + 48 * 3600


def test_receipt_does_not_suppress_completed_result_reply(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'token')
    store.save_page('kf1', '', 'cursor', [message()], {'alice'})
    job, _ = store.enqueue('kf1', 'm1', 'text', {'text': 'original'})
    receipt = store.queue_reply('kf1', 'm1', '已受理', kind='receipt')
    store.mark_reply(receipt['send_id'], 'accepted')
    Database(store.path).update_job(job.id, JobStatus.SUCCEEDED)
    assert len(store.results_to_reply()) == 1
    result = store.queue_reply('kf1', 'm1', '已完成：整理好了')
    assert result['send_id'] != receipt['send_id']
    assert store.results_to_reply() == []
    assert [row['content'] for row in store.outbox()] == ['已完成：整理好了']


def test_overlapping_sync_cannot_clear_a_more_recent_cursor(tmp_path):
    store = store_at(tmp_path)
    store.notify('kf1', 'token')
    generation = store.account('kf1')['generation']
    store.save_page('kf1', '', 'c1', [], {'alice'})
    # The first sync drained c1; a second sync has now committed another page,
    # but fails before draining its remaining pages.
    store.save_page('kf1', 'c1', 'c2', [message()], {'alice'})
    store.finish_sync('kf1', generation, 'c1')
    assert store.accounts_to_sync(), 'A stale completion must not lose pending pages'
