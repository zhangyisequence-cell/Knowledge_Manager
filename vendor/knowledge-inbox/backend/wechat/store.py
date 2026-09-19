"""Durable WeChat inbox/outbox sharing the ingestion database transaction boundary."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from backend.models import Job
from backend.storage import Database


def _identifier(value, name: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Invalid WeChat {name}")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"Invalid WeChat {name}")
    return value


def _key(account: str, message_id: str, kind: str) -> str:
    data = json.dumps([account, message_id, kind], ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()[:32]


class WeChatStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self.path, timeout=20)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        Database(self.path).initialize()
        with self._transaction() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS wechat_accounts (
                    open_kfid TEXT PRIMARY KEY, token TEXT NOT NULL,
                    cursor TEXT NOT NULL DEFAULT '', generation INTEGER NOT NULL DEFAULT 1,
                    pending INTEGER NOT NULL DEFAULT 1, retry_at REAL NOT NULL DEFAULT 0,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS wechat_messages (
                    open_kfid TEXT NOT NULL, msgid TEXT NOT NULL, sender TEXT NOT NULL,
                    sent_at INTEGER NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                    job_id TEXT, error TEXT,
                    PRIMARY KEY (open_kfid, msgid)
                );
                CREATE TABLE IF NOT EXISTS wechat_outbox (
                    send_id TEXT PRIMARY KEY, open_kfid TEXT NOT NULL,
                    source_msgid TEXT NOT NULL, kind TEXT NOT NULL,
                    sender TEXT NOT NULL, content TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', retry_at REAL NOT NULL DEFAULT 0,
                    error TEXT, UNIQUE (open_kfid, source_msgid, kind)
                );
                CREATE TABLE IF NOT EXISTS wechat_reply_batches (
                    batch_id TEXT PRIMARY KEY, open_kfid TEXT NOT NULL, sender TEXT NOT NULL,
                    anchor_msgid TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS wechat_one_open_batch
                    ON wechat_reply_batches(open_kfid,sender) WHERE state='open';
                CREATE TABLE IF NOT EXISTS wechat_reply_sources (
                    open_kfid TEXT NOT NULL, msgid TEXT NOT NULL, batch_id TEXT NOT NULL,
                    PRIMARY KEY(open_kfid,msgid)
                );
                CREATE TABLE IF NOT EXISTS wechat_retries (
                    scope TEXT NOT NULL, identity TEXT NOT NULL, attempts INTEGER NOT NULL,
                    retry_at REAL NOT NULL, error TEXT, PRIMARY KEY(scope,identity)
                );
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(wechat_outbox)")}
            if "attempted_at" not in columns:
                connection.execute("ALTER TABLE wechat_outbox ADD COLUMN attempted_at REAL")

    def notify(self, account: str, token: str):
        _identifier(account, "account")
        _identifier(token, "sync token", 2048)
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO wechat_accounts (open_kfid, token) VALUES (?, ?)
                ON CONFLICT(open_kfid) DO UPDATE SET token=excluded.token,
                    generation=CASE WHEN wechat_accounts.token=excluded.token
                                    THEN wechat_accounts.generation
                                    ELSE wechat_accounts.generation+1 END,
                    pending=1, retry_at=0, error=NULL
            """,
                (account, token),
            )

    def account(self, account: str) -> dict:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM wechat_accounts WHERE open_kfid=?", (account,)
            ).fetchone()
            if not row:
                raise ValueError("Unknown WeChat account")
            return dict(row)

    def accounts_to_sync(self) -> list[dict]:
        with self._transaction() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM wechat_accounts WHERE pending=1 AND retry_at<=?", (time.time(),)
                )
            ]

    def save_page(
        self,
        account: str,
        expected_cursor: str,
        next_cursor: str,
        messages: list[dict],
        allowed_senders: set[str],
    ):
        if not isinstance(next_cursor, str) or len(next_cursor) > 4096:
            raise ValueError("Invalid WeChat cursor")
        if not isinstance(messages, list) or len(messages) > 1000:
            raise ValueError("Invalid WeChat message page")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT cursor FROM wechat_accounts WHERE open_kfid=?", (account,)
            ).fetchone()
            if not row or row["cursor"] != expected_cursor:
                raise ValueError("WeChat cursor changed; fetch again before advancing")
            for message in messages:
                if not isinstance(message, dict) or message.get("open_kfid") != account:
                    raise ValueError("WeChat message account mismatch")
                msgid = _identifier(message.get("msgid"), "message id")
                origin = message.get("origin")
                if type(origin) is not int or origin not in {3, 4, 5}:
                    raise ValueError("Invalid WeChat message origin")
                sender = message.get("external_userid", "")
                if origin == 3:
                    _identifier(sender, "sender")
                elif not isinstance(sender, str):
                    raise ValueError("Invalid WeChat event sender")
                sent_at = message.get("send_time")
                if type(sent_at) is not int or sent_at <= 0:
                    raise ValueError("Invalid WeChat message timestamp")
                payload = json.dumps(message, ensure_ascii=False)
                if len(payload.encode()) > 65536:
                    raise ValueError("WeChat message exceeds storage limit")
                state = "pending" if origin == 3 and sender in allowed_senders else "ignored"
                connection.execute(
                    """
                    INSERT OR IGNORE INTO wechat_messages
                    (open_kfid,msgid,sender,sent_at,payload,state) VALUES (?,?,?,?,?,?)
                """,
                    (account, msgid, sender, sent_at, payload, state),
                )
                event = message.get("event")
                if origin == 5 and message.get("msgtype") == "event" and isinstance(event, dict):
                    if event.get("event_type") == "msg_send_fail" and isinstance(
                        event.get("fail_msgid"), str
                    ):
                        connection.execute(
                            """UPDATE wechat_outbox
                            SET status='failed',error='delivery failure reported by WeChat'
                            WHERE open_kfid=? AND send_id=? AND status IN ('pending','accepted')""",
                            (account, event["fail_msgid"]),
                        )
            connection.execute(
                "UPDATE wechat_accounts SET cursor=? WHERE open_kfid=?", (next_cursor, account)
            )

    def finish_sync(self, account: str, generation: int, expected_cursor: str):
        with self._transaction() as connection:
            connection.execute(
                """UPDATE wechat_accounts SET pending=0, error=NULL
                                  WHERE open_kfid=? AND generation=? AND cursor=?""",
                (account, generation, expected_cursor),
            )

    def retry_sync(self, account: str, error: str, delay: float = 30):
        with self._transaction() as connection:
            connection.execute(
                "UPDATE wechat_accounts SET retry_at=?,error=? WHERE open_kfid=?",
                (time.time() + delay, error, account),
            )

    def pending_messages(self) -> list[dict]:
        with self._transaction() as connection:
            connection.create_function("wechat_key", 3, _key, deterministic=True)
            rows = connection.execute(
                """
                SELECT m.* FROM wechat_messages m LEFT JOIN wechat_retries r
                ON r.scope='media' AND r.identity=wechat_key(m.open_kfid,m.msgid,'media')
                WHERE m.state='pending' AND (r.retry_at IS NULL OR r.retry_at<=?)
                ORDER BY m.sent_at LIMIT 100
            """,
                (time.time(),),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def enqueue(self, account: str, msgid: str, input_type: str, payload: dict) -> tuple[Job, bool]:
        if input_type not in {"text", "url", "file"}:
            raise ValueError("Invalid ingestion input type")
        job = Job(id=_key(account, msgid, "job"), input_type=input_type, payload=payload)
        with self._transaction() as connection:
            message = connection.execute(
                "SELECT * FROM wechat_messages WHERE open_kfid=? AND msgid=?", (account, msgid)
            ).fetchone()
            if not message or message["state"] not in {"pending", "queued"}:
                raise ValueError("WeChat message is not authorized for ingestion")
            created = (
                connection.execute(
                    """
                INSERT OR IGNORE INTO jobs
                (id,status,input_type,payload,created_at,updated_at) VALUES (?,?,?,?,?,?)
            """,
                    (
                        job.id,
                        job.status.value,
                        input_type,
                        json.dumps(payload, ensure_ascii=False),
                        job.created_at.isoformat(),
                        job.updated_at.isoformat(),
                    ),
                ).rowcount
                == 1
            )
            connection.execute(
                "UPDATE wechat_messages SET state='queued',job_id=? WHERE open_kfid=? AND msgid=?",
                (job.id, account, msgid),
            )
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job.id,)).fetchone()
            return Database._job_from_row(row), created

    def queue_reply(self, account: str, msgid: str, content: str, *, kind: str = "result") -> dict:
        if kind not in {"receipt", "result"}:
            raise ValueError("Invalid WeChat reply kind")
        if not content.strip():
            raise ValueError("Empty WeChat reply")
        marker = "\n（已截断，完整内容保存在知识库）"
        if len(content.encode()) > 2048:
            content = (
                content.encode()[: 2048 - len(marker.encode())].decode("utf-8", "ignore") + marker
            )
        send_id = _key(account, msgid, kind)
        with self._transaction() as connection:
            message = connection.execute(
                "SELECT * FROM wechat_messages WHERE open_kfid=? AND msgid=?", (account, msgid)
            ).fetchone()
            if not message or message["state"] == "ignored":
                raise ValueError("Reply target is not an authorized incoming message")
            connection.execute(
                """INSERT OR IGNORE INTO wechat_outbox
                                  (send_id,open_kfid,source_msgid,kind,sender,content) VALUES (?,?,?,?,?,?)""",
                (send_id, account, msgid, kind, message["sender"], content),
            )
            row = connection.execute(
                "SELECT * FROM wechat_outbox WHERE send_id=?", (send_id,)
            ).fetchone()
            return dict(row)

    def outbox(self) -> list[dict]:
        with self._transaction() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM wechat_outbox WHERE status='pending' AND retry_at<=? LIMIT 100",
                    (time.time(),),
                )
            ]

    def mark_reply(self, send_id: str, status: str, error: str | None = None, delay: float = 30):
        if status not in {"pending", "accepted", "failed", "expired"}:
            raise ValueError("Unknown WeChat reply status")
        with self._transaction() as connection:
            connection.execute(
                "UPDATE wechat_outbox SET status=?,error=?,retry_at=? WHERE send_id=?",
                (status, error, time.time() + delay, send_id),
            )

    def results_to_reply(self) -> list[dict]:
        with self._transaction() as connection:
            rows = connection.execute("""
                SELECT m.*,j.status AS job_status,j.error AS job_error,i.data AS item
                FROM wechat_messages m JOIN jobs j ON j.id=m.job_id
                LEFT JOIN items i ON i.id=j.item_id
                LEFT JOIN wechat_outbox o ON o.open_kfid=m.open_kfid AND o.source_msgid=m.msgid
                    AND o.kind='result'
                WHERE m.state='queued' AND j.status IN ('succeeded','failed') AND o.send_id IS NULL
                AND NOT EXISTS (SELECT 1 FROM wechat_reply_sources s JOIN wechat_reply_batches b
                    ON b.batch_id=s.batch_id WHERE s.open_kfid=m.open_kfid AND s.msgid=m.msgid
                    AND b.state='closed') LIMIT 100
            """).fetchall()
            return [dict(row) for row in rows]

    def reply_deadline(self, account: str, sender: str) -> float:
        """Latest allowed incoming message renews the documented reply window."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT MAX(sent_at) FROM wechat_messages "
                "WHERE open_kfid=? AND sender=? AND state!='ignored'",
                (account, sender),
            ).fetchone()
            return (row[0] or 0) + 48 * 60 * 60

    def defer(self, scope: str, identity: str, error: str) -> float:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT attempts FROM wechat_retries WHERE scope=? AND identity=?",
                (scope, identity),
            ).fetchone()
            attempts = min((row[0] if row else 0) + 1, 20)
            delay = min(3600, 30 * 2 ** (attempts - 1))
            connection.execute(
                """INSERT INTO wechat_retries VALUES (?,?,?,?,?)
                ON CONFLICT(scope,identity) DO UPDATE SET attempts=excluded.attempts,
                retry_at=excluded.retry_at,error=excluded.error""",
                (scope, identity, attempts, time.time() + delay, error),
            )
            return delay

    def clear_retry(self, scope: str, identity: str):
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM wechat_retries WHERE scope=? AND identity=?", (scope, identity)
            )

    def fail_message(self, account: str, msgid: str, error: str):
        with self._transaction() as connection:
            connection.execute(
                "UPDATE wechat_messages SET state='failed',error=? WHERE open_kfid=? AND msgid=?",
                (error, account, msgid),
            )

    def receipts_to_reply(self) -> list[dict]:
        with self._transaction() as connection:
            return [
                dict(row)
                for row in connection.execute("""
                SELECT m.* FROM wechat_messages m
                LEFT JOIN wechat_outbox o ON o.open_kfid=m.open_kfid AND o.source_msgid=m.msgid AND o.kind='receipt'
                WHERE m.state='queued' AND o.send_id IS NULL LIMIT 100
            """)
            ]

    def reserve_reply(self, send_id: str) -> bool:
        """Reserve quota before I/O; uncertain attempts count, and reuse their send ID.

        A new incoming customer message starts a new five-reply window. There is
        one worker process. API acceptance is deliberately never called delivery.
        """
        now = time.time()
        with self._transaction() as connection:
            reply = connection.execute(
                "SELECT * FROM wechat_outbox WHERE send_id=?", (send_id,)
            ).fetchone()
            if not reply or reply["status"] != "pending":
                return False
            latest = (
                connection.execute(
                    """SELECT MAX(sent_at) FROM wechat_messages
                WHERE open_kfid=? AND sender=? AND state!='ignored'""",
                    (reply["open_kfid"], reply["sender"]),
                ).fetchone()[0]
                or 0
            )
            if now >= latest + 48 * 3600:
                connection.execute(
                    "UPDATE wechat_outbox SET status='expired',error='reply window expired' WHERE send_id=?",
                    (send_id,),
                )
                return False
            if reply["attempted_at"] is None:
                count = connection.execute(
                    """SELECT COUNT(*) FROM wechat_outbox
                    WHERE open_kfid=? AND sender=? AND attempted_at>=?""",
                    (reply["open_kfid"], reply["sender"], latest),
                ).fetchone()[0]
                if count >= 5:
                    connection.execute(
                        "UPDATE wechat_outbox SET retry_at=?,error='reply quota reached' WHERE send_id=?",
                        (now + 60, send_id),
                    )
                    return False
                connection.execute(
                    "UPDATE wechat_outbox SET attempted_at=? WHERE send_id=?", (now, send_id)
                )
            return True

    def failures_to_reply(self) -> list[dict]:
        with self._transaction() as connection:
            return [
                dict(row)
                for row in connection.execute("""
                SELECT m.* FROM wechat_messages m
                LEFT JOIN wechat_outbox o ON o.open_kfid=m.open_kfid AND o.source_msgid=m.msgid AND o.kind='result'
                WHERE m.state='failed' AND o.send_id IS NULL LIMIT 100
            """)
            ]

    def restrict_authorization(self, accounts: set[str], senders: set[str]):
        """Retired authorizations cannot block current intake or leak queued replies."""
        with self._transaction() as connection:
            connection.create_function(
                "wechat_allowed",
                2,
                lambda account, sender: int(account in accounts and sender in senders),
                deterministic=True,
            )
            connection.execute("""UPDATE wechat_messages SET state='ignored'
                WHERE state!='ignored' AND wechat_allowed(open_kfid,sender)=0""")
            connection.execute("""UPDATE wechat_outbox SET status='expired',error='authorization revoked'
                WHERE status='pending' AND wechat_allowed(open_kfid,sender)=0""")
            connection.execute("""UPDATE wechat_reply_batches SET state='closed'
                WHERE state='open' AND wechat_allowed(open_kfid,sender)=0""")

    def prepare_reply_batches(self):
        with self._transaction() as connection:
            sources = connection.execute("""SELECT m.* FROM wechat_messages m
                LEFT JOIN wechat_reply_sources s ON s.open_kfid=m.open_kfid AND s.msgid=m.msgid
                LEFT JOIN wechat_outbox o ON o.open_kfid=m.open_kfid AND o.source_msgid=m.msgid AND o.kind='result'
                WHERE m.state IN ('pending','queued','failed') AND s.msgid IS NULL AND o.send_id IS NULL
                ORDER BY m.sent_at,m.msgid""").fetchall()
            for source in sources:
                batch = connection.execute(
                    """SELECT batch_id FROM wechat_reply_batches
                    WHERE open_kfid=? AND sender=? AND state='open'""",
                    (source["open_kfid"], source["sender"]),
                ).fetchone()
                batch_id = (
                    batch[0] if batch else _key(source["open_kfid"], source["msgid"], "batch")
                )
                if not batch:
                    connection.execute(
                        "INSERT INTO wechat_reply_batches VALUES (?,?,?,?,'open')",
                        (batch_id, source["open_kfid"], source["sender"], source["msgid"]),
                    )
                connection.execute(
                    "INSERT INTO wechat_reply_sources VALUES (?,?,?)",
                    (source["open_kfid"], source["msgid"], batch_id),
                )

    def reply_batches(self) -> list[dict]:
        with self._transaction() as connection:
            batches = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM wechat_reply_batches WHERE state='open'"
                )
            ]
            for batch in batches:
                batch["sources"] = [
                    dict(row)
                    for row in connection.execute(
                        """
                    SELECT m.*,j.status AS job_status,i.data AS item
                    FROM wechat_reply_sources s JOIN wechat_messages m
                    ON m.open_kfid=s.open_kfid AND m.msgid=s.msgid
                    LEFT JOIN jobs j ON j.id=m.job_id LEFT JOIN items i ON i.id=j.item_id
                    WHERE s.batch_id=? AND m.state!='ignored' ORDER BY m.sent_at,m.msgid
                """,
                        (batch["batch_id"],),
                    )
                ]
            return batches

    def finish_reply_batch(self, batch_id: str, content: str):
        # Result insertion and batch closure share a commit: a crash cannot let
        # new messages join a batch whose immutable result has already been sent.
        content = content.encode("utf-8")[:2000].decode("utf-8", "ignore")
        with self._transaction() as connection:
            batch = connection.execute(
                "SELECT * FROM wechat_reply_batches WHERE batch_id=? AND state='open'", (batch_id,)
            ).fetchone()
            if not batch:
                return
            pending = connection.execute(
                """SELECT COUNT(*) FROM wechat_reply_sources s
                JOIN wechat_messages m ON m.open_kfid=s.open_kfid AND m.msgid=s.msgid
                LEFT JOIN jobs j ON j.id=m.job_id WHERE s.batch_id=? AND
                (m.state='pending' OR (m.state='queued' AND (j.status IS NULL OR j.status NOT IN ('succeeded','failed'))))
            """,
                (batch_id,),
            ).fetchone()[0]
            if pending:
                return
            connection.execute(
                """INSERT OR IGNORE INTO wechat_outbox
                (send_id,open_kfid,source_msgid,kind,sender,content) VALUES (?,?,?,'result',?,?)""",
                (
                    _key(batch["open_kfid"], batch["anchor_msgid"], "result"),
                    batch["open_kfid"],
                    batch["anchor_msgid"],
                    batch["sender"],
                    content,
                ),
            )
            connection.execute(
                "UPDATE wechat_reply_batches SET state='closed' WHERE batch_id=?", (batch_id,)
            )
