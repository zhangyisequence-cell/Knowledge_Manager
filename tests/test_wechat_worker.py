import asyncio
import json
import sqlite3
import time
from pathlib import Path

import pytest
from backend.models import JobStatus
from backend.storage import Database
from backend.worker import IngestionWorker
from test_wechat_store import message, store_at


class API:
    def __init__(self):
        self.sent = []
        self.media_error = False
        self.send_error = False

    async def sync_messages(self, account, token, cursor):
        return {"has_more": 0, "next_cursor": cursor, "msg_list": []}

    async def download_media(self, media_id, destination, *, max_bytes):
        if self.media_error:
            raise RuntimeError("secret path /srv/private")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"content")
        return destination

    async def send_text(self, account, recipient, send_id, content):
        self.sent.append((account, recipient, send_id, content))
        if self.send_error:
            raise RuntimeError("private token")
        return {"errcode": 0, "msgid": send_id}


def setup(tmp_path, messages=None):
    from backend.wechat.worker import WeChatWorker

    store = store_at(tmp_path)
    store.notify("kf1", "token")
    messages = messages or [message()]
    store.save_page(
        "kf1",
        "",
        "cursor",
        [dict(m, send_time=int(time.time())) for m in messages],
        {"alice"},
    )
    ingestion = IngestionWorker(Database(store.path), None)
    api = API()
    worker = WeChatWorker(
        store, api, ingestion, tmp_path / "originals", {"kf1"}, {"alice"}
    )
    return store, ingestion, api, worker


def rows(store, table):
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(r) for r in connection.execute("SELECT * FROM " + table)]


def test_duplicate_cycle_and_restart_do_not_reenqueue_or_reset_jobs(tmp_path):
    store, ingestion, _api, worker = setup(tmp_path)
    asyncio.run(worker.cycle())
    job = ingestion.database.list_jobs()[0]
    assert ingestion.queue.qsize() == 1
    ingestion.database.update_job(job.id, JobStatus.SUCCEEDED)
    asyncio.run(worker.cycle())
    asyncio.run(worker.cycle())
    assert len(ingestion.database.list_jobs()) == 1
    assert ingestion.database.get_job(job.id).status == JobStatus.SUCCEEDED
    assert ingestion.queue.qsize() == 1
    replies = rows(store, "wechat_outbox")
    assert {r["kind"] for r in replies} == {"receipt", "result"}
    assert all(r["status"] == "accepted" for r in replies)
    assert len({r["send_id"] for r in replies}) == 2


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://example.org/a?x=1", "url"),
        ("See https://example.org/a", "text"),
        ("https://", "text"),
        ("https://example.org/a\nnotes", "text"),
        ("ftp://example.org", "text"),
    ],
)
def test_only_whole_http_url_is_ingested_as_url(tmp_path, text, expected):
    m = dict(message(), text={"content": text})
    _store, ingestion, _api, worker = setup(tmp_path, [m])
    asyncio.run(worker.cycle())
    assert ingestion.database.list_jobs()[0].input_type == expected


def test_media_failure_retries_durably_before_any_job_or_receipt(tmp_path):
    m = dict(message(), msgtype="image", image={"media_id": "media"})
    store, ingestion, api, worker = setup(tmp_path, [m])
    api.media_error = True
    asyncio.run(worker.cycle())
    assert ingestion.database.list_jobs() == []
    assert rows(store, "wechat_outbox") == []
    assert store.pending_messages() == []
    assert "private" not in json.dumps(rows(store, "wechat_retries"))
    with store._transaction() as connection:
        connection.execute("UPDATE wechat_retries SET retry_at=0")
    api.media_error = False
    asyncio.run(worker.cycle())
    job = ingestion.database.list_jobs()[0]
    assert Path(job.payload["path"]).read_bytes() == b"content"
    assert Path(job.payload["path"]).parent == tmp_path / "originals"


def test_failed_result_never_copies_internal_exception(tmp_path):
    store, ingestion, _api, worker = setup(tmp_path)
    asyncio.run(worker.cycle())
    job = ingestion.database.list_jobs()[0]
    ingestion.database.update_job(
        job.id, JobStatus.FAILED, error="password secret /srv/private"
    )
    asyncio.run(worker.cycle())
    result = next(r for r in rows(store, "wechat_outbox") if r["kind"] == "result")
    assert "secret" not in result["content"] and "/srv" not in result["content"]
    assert "失败" in result["content"]


def test_legacy_pending_replies_still_respect_expiry_and_quota(tmp_path):
    store, _ingestion, api, worker = setup(
        tmp_path, [message("m" + str(i)) for i in range(6)]
    )
    # Pending individual replies from an older deployment still obey the hard cap.
    for index in range(6):
        store.queue_reply("kf1", "m" + str(index), "legacy receipt", kind="receipt")
    asyncio.run(worker.cycle())
    assert len(api.sent) == 5
    assert sum(r["status"] == "accepted" for r in rows(store, "wechat_outbox")) == 5
    with store._transaction() as connection:
        connection.execute(
            "UPDATE wechat_messages SET sent_at=?", (int(time.time()) - 49 * 3600,)
        )
        connection.execute("UPDATE wechat_outbox SET retry_at=0")
    asyncio.run(worker.cycle())
    assert len(api.sent) == 5
    assert any(r["status"] == "expired" for r in rows(store, "wechat_outbox"))


def test_uncertain_send_retries_same_id_after_restart(tmp_path):
    store, _ingestion, api, worker = setup(tmp_path)
    api.send_error = True
    asyncio.run(worker.cycle())
    first = api.sent[0][2]
    assert rows(store, "wechat_outbox")[0]["status"] == "pending"
    with store._transaction() as connection:
        connection.execute("UPDATE wechat_outbox SET retry_at=0")
    api.send_error = False
    asyncio.run(worker.cycle())
    assert api.sent[-1][2] == first
    assert rows(store, "wechat_outbox")[0]["status"] == "accepted"


def test_worker_stop_cancels_blocked_network(tmp_path):
    store, _ingestion, api, worker = setup(tmp_path)

    async def blocked(*args):
        await asyncio.Event().wait()

    api.sync_messages = blocked

    async def run():
        await worker.start()
        await asyncio.sleep(0.03)
        await asyncio.wait_for(worker.stop(), timeout=1)

    asyncio.run(run())
    assert store.accounts_to_sync()


def test_runtime_disabled_and_incomplete_config_fail_closed(monkeypatch):
    from backend.wechat.runtime import create_public_app, settings_from_env

    for name in list(__import__("os").environ):
        if name.startswith("WECHAT_"):
            monkeypatch.delenv(name)
    assert settings_from_env() is None
    with pytest.raises(ValueError):
        create_public_app()
    monkeypatch.setenv("WECHAT_ENABLED", "true")
    with pytest.raises(ValueError):
        settings_from_env()


def test_restart_recovers_committed_job_and_missing_receipt(tmp_path):
    from backend.models import ContentItem
    from backend.wechat.worker import WeChatWorker

    store, _ingestion, api, worker = setup(tmp_path)
    job, _ = store.enqueue("kf1", "m1", "text", {"text": "persisted before crash"})

    class Pipeline:
        async def process(self, recovered):
            assert recovered.id == job.id
            return ContentItem(id=job.id, source_type="text"), Path("note.md")

    reopened = store_at(tmp_path)
    recovered = IngestionWorker(Database(store.path), Pipeline())
    worker = WeChatWorker(
        reopened, api, recovered, tmp_path / "originals", {"kf1"}, {"alice"}
    )

    async def run():
        await recovered.start()
        await asyncio.wait_for(recovered.queue.join(), timeout=2)
        await worker.cycle()
        await recovered.stop()

    asyncio.run(run())
    assert recovered.database.get_job(job.id).status == JobStatus.SUCCEEDED
    assert {r["kind"] for r in rows(store, "wechat_outbox")} == {"receipt", "result"}


@pytest.mark.parametrize(
    "kind,limit,suffix",
    [
        ("image", 2097152, ".jpg"),
        ("voice", 2097152, ".amr"),
        ("video", 10485760, ".mp4"),
        ("file", 20971520, ".pdf"),
    ],
)
def test_media_limits_safe_names_and_complete_bytes(tmp_path, kind, limit, suffix):
    m = dict(message(), msgtype=kind)
    m[kind] = {"media_id": "m", "name": "../../escape.exe"}
    _store, ingestion, api, worker = setup(tmp_path, [m])

    async def download(media_id, destination, *, max_bytes):
        assert max_bytes == limit
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.7\ncontent")
        return destination

    api.download_media = download
    asyncio.run(worker.cycle())
    saved = Path(ingestion.database.list_jobs()[0].payload["path"])
    assert saved.suffix == suffix and saved.parent == tmp_path / "originals"
    assert saved.read_bytes() == b"%PDF-1.7\ncontent"


def test_failed_send_event_updates_acceptance_without_claiming_delivery(tmp_path):
    store, _ingestion, _api, worker = setup(tmp_path)
    asyncio.run(worker.cycle())
    send_id = rows(store, "wechat_outbox")[0]["send_id"]
    event = dict(
        message("failure", origin=5),
        msgtype="event",
        event={"event_type": "msg_send_fail", "fail_msgid": send_id, "fail_type": 1},
    )
    store.save_page("kf1", "cursor", "event-cursor", [event], {"alice"})
    asyncio.run(worker.cycle())
    reply = rows(store, "wechat_outbox")[0]
    assert reply["status"] == "failed"
    assert "delivery" in reply["error"]


def test_failed_input_result_survives_crash_between_state_and_reply(tmp_path):
    store, ingestion, _api, worker = setup(tmp_path)
    store.fail_message("kf1", "m1", "unsupported content")
    asyncio.run(worker.cycle())
    assert rows(store, "wechat_outbox")[0]["kind"] == "result"
    assert ingestion.database.list_jobs() == []


def test_retrying_old_media_cannot_starve_new_messages(tmp_path):
    from backend.wechat.store import _key

    messages = [message("m" + str(i)) for i in range(101)]
    store, ingestion, _api, worker = setup(tmp_path, messages)
    for m in messages[:100]:
        store.defer("media", _key("kf1", m["msgid"], "media"), "unavailable")
    asyncio.run(worker.cycle())
    assert len(ingestion.database.list_jobs()) == 1


def test_completed_item_returns_summary_without_internal_note_path(tmp_path):
    from backend.models import ContentItem

    store, ingestion, _api, worker = setup(tmp_path)
    asyncio.run(worker.cycle())
    job = ingestion.database.list_jobs()[0]
    item = ContentItem(
        id=job.id,
        source_type="text",
        title="每周复盘",
        category="工作方法",
        summary="每周复盘能帮助及时调整计划。",
    )
    ingestion.database.save_item(item, "/srv/private/note.md")
    ingestion.database.update_job(
        job.id, JobStatus.SUCCEEDED, item_id=item.id, note_path="/srv/private/note.md"
    )
    asyncio.run(worker.cycle())
    result = next(r for r in rows(store, "wechat_outbox") if r["kind"] == "result")
    assert item.summary in result["content"] and "/srv/private" not in result["content"]
    assert item.title in result["content"] and item.category in result["content"]
    assert job.id[:8] in result["content"]


def test_public_runtime_factory_has_only_callback_routes(tmp_path, monkeypatch):
    import base64

    from backend.config import AppConfig
    from backend.wechat.runtime import create_public_app

    values = {
        "WECHAT_ENABLED": "true",
        "WECHAT_CORP_ID": "synthetic-corp",
        "WECHAT_SECRET": "synthetic-secret",
        "WECHAT_CALLBACK_TOKEN": "synthetic-token",
        "WECHAT_ENCODING_AES_KEY": base64.b64encode(bytes(range(32)))
        .decode()
        .rstrip("="),
        "WECHAT_ALLOWED_ACCOUNTS": "kf1",
        "WECHAT_ALLOWED_SENDERS": "alice",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    config = AppConfig(data_dir=tmp_path, database_path=tmp_path / "db.sqlite")
    monkeypatch.setattr("backend.config.get_config", lambda: config)
    app = create_public_app()
    assert {route.path for route in app.routes} == {"/wechat/callback"}


@pytest.mark.parametrize(
    "content,suffix",
    [
        (b"RIFF" + b"\x20\x00\x00\x00" + b"WAVEfmt " + b"\0" * 20, ".wav"),
        (b"#!AMR\n" + b"\0" * 16, ".amr"),
        (b"\x00\x00\x00\x18ftypmp42" + b"\0" * 20, ".mp4"),
        (b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x64" + b"\0" * 64, ".mp3"),
        (b"fLaC" + b"\x00" * 20, ".flac"),
    ],
)
def test_generic_file_media_is_routed_to_media_adapter(tmp_path, content, suffix):
    m = dict(message(), msgtype="file", file={"media_id": "media"})
    _store, ingestion, api, worker = setup(tmp_path, [m])

    async def download(media_id, destination, *, max_bytes):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return destination

    api.download_media = download
    asyncio.run(worker.cycle())
    assert Path(ingestion.database.list_jobs()[0].payload["path"]).suffix == suffix


def test_invalid_media_id_fails_durably_without_endless_retry(tmp_path):
    from backend.wechat.client import WeChatAPIError

    m = dict(message(), msgtype="file", file={"media_id": "expired"})
    store, ingestion, api, worker = setup(tmp_path, [m])

    async def download(*args, **kwargs):
        raise WeChatAPIError("WeChat API error code 40007", code=40007)

    api.download_media = download
    asyncio.run(worker.cycle())
    assert rows(store, "wechat_messages")[0]["state"] == "failed"
    assert ingestion.database.list_jobs() == []
    assert "重新发送" in rows(store, "wechat_outbox")[0]["content"]


def test_revoked_pending_and_results_cannot_starve_authorized_messages(tmp_path):
    from backend.wechat.worker import WeChatWorker

    messages = [message("m" + str(i)) for i in range(101)]
    store, ingestion, api, _worker = setup(tmp_path, messages)
    for m in messages[:100]:
        job, _ = store.enqueue("kf1", m["msgid"], "text", {"text": "revoked"})
        ingestion.database.update_job(job.id, JobStatus.SUCCEEDED)
    with store._transaction() as connection:
        connection.execute(
            "UPDATE wechat_messages SET sender='revoked' WHERE msgid!='m100'"
        )
    worker = WeChatWorker(
        store, api, ingestion, tmp_path / "originals", {"kf1"}, {"alice"}
    )
    asyncio.run(worker.cycle())
    assert ingestion.queue.qsize() == 1
    assert all(row["sender"] == "alice" for row in rows(store, "wechat_outbox"))
    assert store.results_to_reply() == []


@pytest.mark.parametrize(
    "kind,suffix,content",
    [
        ("image", ".jpg", b"\xff\xd8\xfftest"),
        ("file", ".bin", b"%PDF-1.7\nbody"),
        ("file", ".pdf", b"%PDF-1.7\nbody"),
    ],
)
def test_restart_reuses_atomic_media_before_enqueue_when_remote_expired(
    tmp_path, kind, suffix, content
):
    from backend.wechat.client import WeChatAPIError
    from backend.wechat.store import _key
    from backend.wechat.worker import WeChatWorker

    m = dict(message(), msgtype=kind)
    m[kind] = {"media_id": "expired"}
    store, ingestion, api, _worker = setup(tmp_path, [m])
    originals = tmp_path / "originals"
    originals.mkdir()
    saved = originals / (_key("kf1", "m1", "media") + suffix)
    saved.write_bytes(content)

    async def expired(*args, **kwargs):
        raise WeChatAPIError("expired media", code=40007)

    api.download_media = expired
    worker = WeChatWorker(
        store_at(tmp_path), api, ingestion, originals, {"kf1"}, {"alice"}
    )
    asyncio.run(worker.cycle())
    job = ingestion.database.list_jobs()[0]
    assert Path(job.payload["path"]).read_bytes() == content
    assert Path(job.payload["path"]).suffix == (".pdf" if kind == "file" else ".jpg")
    assert rows(store, "wechat_messages")[0]["state"] == "queued"


@pytest.mark.parametrize("invalid", ["part", "oversize", "directory"])
def test_recovery_never_enqueues_partial_or_invalid_cached_media(tmp_path, invalid):
    from backend.wechat.client import WeChatAPIError
    from backend.wechat.store import _key

    m = dict(message(), msgtype="image", image={"media_id": "expired"})
    _store, ingestion, api, worker = setup(tmp_path, [m])
    worker.originals.mkdir()
    saved = worker.originals / (_key("kf1", "m1", "media") + ".jpg")
    if invalid == "part":
        saved.with_suffix(".jpg.part").write_bytes(b"\xff\xd8\xffpartial")
    elif invalid == "directory":
        saved.mkdir()
    else:
        saved.write_bytes(b"x" * (2097152 + 1))

    async def expired(*args, **kwargs):
        raise WeChatAPIError("expired media", code=40007)

    api.download_media = expired
    asyncio.run(worker.cycle())
    assert ingestion.database.list_jobs() == []


def test_sender_burst_coalesces_receipt_and_completed_results(tmp_path):
    store, ingestion, api, worker = setup(
        tmp_path, [message("m" + str(i)) for i in range(6)]
    )
    asyncio.run(worker.cycle())
    assert len(api.sent) == 1
    for job in ingestion.database.list_jobs():
        ingestion.database.update_job(job.id, JobStatus.SUCCEEDED)
    asyncio.run(worker.cycle())
    replies = rows(store, "wechat_outbox")
    assert len(replies) == 2
    assert {r["kind"] for r in replies} == {"receipt", "result"}
    result = next(r for r in replies if r["kind"] == "result")
    assert "6" in result["content"] and result["status"] == "accepted"
    assert len(result["content"].encode()) <= 2048
    assert store.results_to_reply() == []


def test_sender_batch_waits_for_staggered_jobs_across_restart(tmp_path):
    from backend.wechat.worker import WeChatWorker

    store, ingestion, api, worker = setup(
        tmp_path, [message("m" + str(i)) for i in range(7)]
    )
    asyncio.run(worker.cycle())
    for job in ingestion.database.list_jobs()[:-1]:
        ingestion.database.update_job(job.id, JobStatus.SUCCEEDED)
        asyncio.run(worker.cycle())
    assert len(api.sent) == 1
    last = ingestion.database.list_jobs()[-1]
    ingestion.database.update_job(last.id, JobStatus.FAILED, error="secret detail")
    restarted = WeChatWorker(
        store_at(tmp_path), api, ingestion, tmp_path / "originals", {"kf1"}, {"alice"}
    )
    asyncio.run(restarted.cycle())
    asyncio.run(restarted.cycle())
    assert len(api.sent) == 2
    result = next(r for r in rows(store, "wechat_outbox") if r["kind"] == "result")
    assert "失败" in result["content"] and "secret" not in result["content"]
    assert len(rows(store, "wechat_reply_sources")) == 7


def test_recovery_rejects_symlink_to_outside_originals(tmp_path):
    from backend.wechat.store import _key

    m = dict(message(), msgtype="file", file={"media_id": "media"})
    _store, ingestion, _api, worker = setup(tmp_path, [m])
    worker.originals.mkdir()
    outside = tmp_path / "private.pdf"
    outside.write_bytes(b"%PDF-1.7\nprivate")
    candidate = worker.originals / (_key("kf1", "m1", "media") + ".pdf")
    try:
        candidate.symlink_to(outside)
    except OSError:
        pytest.skip("This Windows account cannot create symlinks")
    asyncio.run(worker.cycle())
    assert ingestion.database.list_jobs() == []
    assert outside.read_bytes() == b"%PDF-1.7\nprivate"


def test_saved_media_wrong_declared_type_is_not_reused(tmp_path):
    from backend.wechat.store import _key

    m = dict(message(), msgtype="image", image={"media_id": "media"})
    _store, ingestion, _api, worker = setup(tmp_path, [m])
    worker.originals.mkdir()
    (worker.originals / (_key("kf1", "m1", "media") + ".jpg")).write_bytes(
        b"%PDF-1.7\nbody"
    )
    asyncio.run(worker.cycle())
    assert ingestion.database.list_jobs() == []


def test_malformed_content_failure_does_not_poison_other_sender_replies(tmp_path):
    store, ingestion, _api, worker = setup(tmp_path, [dict(message('bad'), text='malformed')])
    worker.allowed_senders.add('bob')
    good = dict(message('good'), external_userid='bob', send_time=int(time.time()))
    store.save_page('kf1', 'cursor', 'cursor2', [good], {'alice', 'bob'})
    asyncio.run(worker.cycle())
    jobs = ingestion.database.list_jobs()
    assert len(jobs) == 1
    ingestion.database.update_job(jobs[0].id, JobStatus.SUCCEEDED)
    asyncio.run(worker.cycle())
    replies = rows(store, 'wechat_outbox')
    assert any(row['sender'] == 'alice' and row['kind'] == 'result'
               and row['status'] == 'accepted' and '失败' in row['content'] for row in replies)
    assert any(row['sender'] == 'bob' and row['kind'] == 'result'
               and row['status'] == 'accepted' for row in replies)
