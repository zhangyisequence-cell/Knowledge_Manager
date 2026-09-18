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
            """)

    def notify(self, account: str, token: str):
        _identifier(account, "account")
        _identifier(token, "sync token", 2048)
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO wechat_accounts (open_kfid, token) VALUES (?, ?)
                ON CONFLICT(open_kfid) DO UPDATE SET token=excluded.token,
                    generation=generation+1, pending=1, retry_at=0, error=NULL
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
            rows = connection.execute(
                "SELECT * FROM wechat_messages WHERE state='pending' ORDER BY sent_at LIMIT 100"
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

    def queue_reply(self, account: str, msgid: str, content: str, *, kind: str = 'result') -> dict:
        if kind not in {'receipt', 'result'}:
            raise ValueError('Invalid WeChat reply kind')
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

    def mark_reply(self, send_id: str, status: str, error: str | None = None):
        if status not in {"pending", "accepted", "failed", "expired"}:
            raise ValueError("Unknown WeChat reply status")
        with self._transaction() as connection:
            connection.execute(
                "UPDATE wechat_outbox SET status=?,error=?,retry_at=? WHERE send_id=?",
                (status, error, time.time() + 30, send_id),
            )

    def results_to_reply(self) -> list[dict]:
        with self._transaction() as connection:
            rows = connection.execute("""
                SELECT m.*,j.status AS job_status,j.error AS job_error,i.data AS item
                FROM wechat_messages m JOIN jobs j ON j.id=m.job_id
                LEFT JOIN items i ON i.id=j.item_id
                LEFT JOIN wechat_outbox o ON o.open_kfid=m.open_kfid AND o.source_msgid=m.msgid
                    AND o.kind='result'
                WHERE j.status IN ('succeeded','failed') AND o.send_id IS NULL LIMIT 100
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
