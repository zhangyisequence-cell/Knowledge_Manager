"""One durable, restartable customer-service intake and reply loop."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import stat
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from backend.wechat.client import WeChatAPIError
from backend.wechat.store import WeChatStore, _key
from backend.wechat.sync import synchronize_account

logger = logging.getLogger(__name__)
MEDIA = {"image": (2, ".jpg"), "voice": (2, ".amr"), "video": (10, ".mp4"), "file": (20, ".bin")}


def _url(value):
    if not isinstance(value, str) or not value or re.search(r"\s|[\x00-\x1f\\]", value):
        return False
    try:
        parts = urlsplit(value)
        return (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and not parts.username
            and parts.port != 0
        )
    except ValueError:
        return False


def _file_suffix(path):
    """Official file messages may omit names; inspect bounded, already downloaded bytes."""
    header = path.read_bytes()
    # Signatures select an adapter only; decoding still determines ingestion success.
    if len(header) >= 12 and header[:4] == b"RIFF":
        if header[8:12] == b"WAVE":
            return ".wav"
        if header[8:12] == b"AVI ":
            return ".avi"
    if header.startswith((b"#!AMR\n", b"#!AMR-WB\n")):
        return ".amr"
    if header.startswith(b"fLaC"):
        return ".flac"
    if header.startswith(b"OggS"):
        return ".ogg"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand in {b"M4A ", b"M4B "}:
            return ".m4a"
        if brand == b"qt  ":
            return ".mov"
        if brand in {b"isom", b"iso2", b"avc1", b"mp41", b"mp42", b"dash", b"M4V "}:
            return ".mp4"
    if header.startswith(bytes.fromhex("1a45dfa3")):
        if b"webm" in header[:4096]:
            return ".webm"
        if b"matroska" in header[:4096]:
            return ".mkv"
    frame = header
    if len(header) >= 10 and header[:3] == b"ID3" and all(v < 128 for v in header[6:10]):
        size = sum(v << shift for v, shift in zip(header[6:10], (21, 14, 7, 0)))
        frame = header[10 + size + (10 if header[5] & 0x10 else 0) :]
    if len(frame) >= 4 and frame[0] == 255:
        if (frame[1] & 0xF6) == 0xF0 and ((frame[2] >> 2) & 15) < 13:
            return ".aac"
        if (frame[1] & 0xE0) == 0xE0 and (frame[1] & 0x18) != 8 and (frame[1] & 6) != 0:
            if 0 < (frame[2] >> 4) < 15 and (frame[2] & 12) != 12:
                return ".mp3"
    if header.startswith(b"%PDF-"):
        return ".pdf"
    if header.startswith(b"PK"):
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "word/document.xml" in names:
                return ".docx"
            if "xl/workbook.xml" in names:
                return ".xlsx"
        raise ValueError("unsupported attachment")
    if header.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        if "WordDocument".encode("utf-16le") in header:
            return ".doc"
        if "Workbook".encode("utf-16le") in header or "Book".encode("utf-16le") in header:
            return ".xls"
        raise ValueError("unsupported attachment")
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if b"\0" not in header:
        header.decode("utf-8-sig")
        return ".txt"
    raise ValueError("unsupported attachment")


class WeChatWorker:
    def __init__(
        self,
        store: WeChatStore,
        api,
        ingestion,
        originals: Path,
        allowed_accounts: set[str],
        allowed_senders: set[str],
        *,
        interval=2,
    ):
        self.store, self.api, self.ingestion = store, api, ingestion
        self.originals = originals
        self.allowed_accounts, self.allowed_senders = set(allowed_accounts), set(allowed_senders)
        self.interval = interval
        self._task = None

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="wechat-worker")

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self):
        while True:
            try:
                await self.cycle()
            except Exception:
                # Never log upstream exceptions: they can contain credentials or content.
                logger.error("WeChat cycle could not persist state; retrying")
            await asyncio.sleep(self.interval)

    async def cycle(self):
        await asyncio.to_thread(
            self.store.restrict_authorization, self.allowed_accounts, self.allowed_senders
        )
        for account in await asyncio.to_thread(self.store.accounts_to_sync):
            if account["open_kfid"] not in self.allowed_accounts:
                continue
            try:
                await synchronize_account(self.store, self.api, account, self.allowed_senders)
            except Exception:
                delay = await asyncio.to_thread(
                    self.store.defer, "sync", account["open_kfid"], "sync unavailable"
                )
                await asyncio.to_thread(
                    self.store.retry_sync, account["open_kfid"], "sync unavailable", delay
                )
            else:
                await asyncio.to_thread(self.store.clear_retry, "sync", account["open_kfid"])
        for message in await asyncio.to_thread(self.store.pending_messages):
            if not self._allowed(message):
                continue
            await self._ingest(message)
        await asyncio.to_thread(self.store.prepare_reply_batches)
        for batch in await asyncio.to_thread(self.store.reply_batches):
            sources = batch["sources"]
            if not sources or not self._allowed(batch):
                continue
            if any(source["state"] == "queued" for source in sources):
                await asyncio.to_thread(
                    self.store.queue_reply,
                    batch["open_kfid"],
                    batch["anchor_msgid"],
                    "已受理这批资料，全部整理完成后会合并返回结果。",
                    kind="receipt",
                )
            if any(
                source["state"] == "pending"
                or (
                    source["state"] == "queued"
                    and source["job_status"] not in {"succeeded", "failed"}
                )
                for source in sources
            ):
                continue
            content = self._batch_result(sources)
            await asyncio.to_thread(self.store.finish_reply_batch, batch["batch_id"], content)
        for reply in await asyncio.to_thread(self.store.outbox):
            if not self._allowed(reply):
                continue
            if not await asyncio.to_thread(self.store.reserve_reply, reply["send_id"]):
                continue
            try:
                await self.api.send_text(
                    reply["open_kfid"], reply["sender"], reply["send_id"], reply["content"]
                )
            except Exception:
                delay = await asyncio.to_thread(
                    self.store.defer, "send", reply["send_id"], "send unavailable"
                )
                await asyncio.to_thread(
                    self.store.mark_reply, reply["send_id"], "pending", "send unavailable", delay
                )
            else:
                await asyncio.to_thread(self.store.mark_reply, reply["send_id"], "accepted")
                await asyncio.to_thread(self.store.clear_retry, "send", reply["send_id"])

    @staticmethod
    def _batch_result(sources):
        succeeded = sum(source["job_status"] == "succeeded" for source in sources)
        failed = len(sources) - succeeded
        lines = [
            f"本批 {len(sources)} 项：完成 {succeeded} 项，失败 {failed} 项。",
            "完整内容在知识库，任务详情在管理页。",
        ]
        visible = sources[:8]
        per_item_bytes = 1700 // len(visible)
        for index, source in enumerate(visible, 1):
            item = json.loads(source["item"]) if source["item"] else {}
            payload = json.loads(source["payload"])
            message_type = payload.get("msgtype")
            detail = payload.get(message_type)
            if not isinstance(detail, dict):
                detail = {}
            input_title = detail.get("content") if message_type == "text" else detail.get("title")
            title = str(item.get("title") or input_title or message_type or "资料").replace(
                "\n", " "
            )[:20]
            identifier = (source["job_id"] or _key(source["open_kfid"], source["msgid"], "job"))[:8]
            status = "已保存" if source["job_status"] == "succeeded" else "失败，请检查后重新发送"
            line = f"{index}. {title} [{identifier}] {status}"
            if item.get("category"):
                line += "；分类：" + str(item["category"])[:10]
            if source["job_status"] == "succeeded" and item.get("summary"):
                line += "；摘要：" + str(item["summary"]).strip()[:450]
            encoded = line.encode("utf-8")
            if len(encoded) > per_item_bytes:
                line = encoded[: per_item_bytes - 3].decode("utf-8", "ignore") + "…"
            lines.append(line)
        if len(sources) > len(visible):
            lines.append(f"其余 {len(sources) - len(visible)} 项状态请在管理页查看。")
        return "\n".join(lines)

    def _allowed(self, row):
        return row["open_kfid"] in self.allowed_accounts and row["sender"] in self.allowed_senders

    async def _ingest(self, row):
        key = _key(row["open_kfid"], row["msgid"], "media")
        try:
            kind, payload = await self._input(row, key)
        except (ValueError, UnicodeError, zipfile.BadZipFile):
            await asyncio.to_thread(
                self.store.fail_message,
                row["open_kfid"],
                row["msgid"],
                "unsupported or invalid content",
            )
            return
        except Exception as error:
            permanent = isinstance(error, WeChatAPIError) and (
                error.code in {40007, 41005, 45001}
                or str(error) == "WeChat media exceeds size limit"
            )
            if permanent:
                await asyncio.to_thread(
                    self.store.fail_message,
                    row["open_kfid"],
                    row["msgid"],
                    "attachment expired, invalid, or too large",
                )
            else:
                await asyncio.to_thread(
                    self.store.defer, "media", key, "media download unavailable"
                )
            return
        job, created = await asyncio.to_thread(
            self.store.enqueue, row["open_kfid"], row["msgid"], kind, payload
        )
        # enqueue already committed the job; submit() would overwrite an existing result.
        # Existing IngestionWorker.start recovers committed queued/running jobs on restart.
        if created:
            self.ingestion.queue.put_nowait(job.id)
        await asyncio.to_thread(self.store.clear_retry, "media", key)

    def _validate_original(self, path, max_bytes):
        root = self.originals.resolve()
        if self.originals.is_symlink() or path.parent.resolve() != root:
            raise ValueError("invalid original location")
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or path.is_symlink()
            or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & 0x400
            or not 0 < info.st_size <= max_bytes
            or path.resolve().parent != root
        ):
            raise ValueError("invalid original file")
        return path

    def _saved_original(self, key, kind, max_bytes):
        # Only final deterministic names are considered. Transport .part files
        # are never candidates. A final name means the atomic download completed,
        # even when the process died before any database metadata was committed.
        suffixes = [MEDIA[kind][1]]
        if kind == "file":
            suffixes += [
                ".pdf",
                ".docx",
                ".xlsx",
                ".doc",
                ".xls",
                ".txt",
                ".jpg",
                ".png",
                ".wav",
                ".avi",
                ".amr",
                ".flac",
                ".ogg",
                ".m4a",
                ".mov",
                ".mp4",
                ".webm",
                ".mkv",
                ".aac",
                ".mp3",
            ]
        for suffix in suffixes:
            path = self.originals / (key + suffix)
            if not path.exists() and not path.is_symlink():
                continue
            self._validate_original(path, max_bytes)
            detected = _file_suffix(path)
            if (
                kind != "file"
                and detected
                not in {"image": {".jpg", ".png"}, "voice": {".amr"}, "video": {".mp4"}}[kind]
            ):
                raise ValueError("original type mismatch")
            if kind == "file":
                if suffix != ".bin" and detected != suffix:
                    raise ValueError("original type mismatch")
            return path
        return None

    async def _input(self, row, key):
        message = row["payload"]
        kind = message.get("msgtype")
        data = message.get(kind)
        if not isinstance(data, dict):
            raise ValueError("invalid content")
        if kind == "text":
            text = data.get("content")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty text")
            text = text.strip()
            return (
                ("url", {"url": text})
                if _url(text)
                else ("text", {"text": text, "source_type": "wechat"})
            )
        if kind == "link":
            url = data.get("url")
            if not _url(url):
                raise ValueError("invalid link")
            return "url", {"url": url}
        if kind not in MEDIA or not isinstance(data.get("media_id"), str) or not data["media_id"]:
            raise ValueError("unsupported message")
        mb, suffix = MEDIA[kind]
        destination = self.originals / (key + suffix)
        max_bytes = mb * 1024 * 1024
        path = await asyncio.to_thread(self._saved_original, key, kind, max_bytes)
        if path is None:
            path = await self.api.download_media(data["media_id"], destination, max_bytes=max_bytes)
            if path != destination:
                raise ValueError("unexpected downloaded original path")
            await asyncio.to_thread(self._validate_original, path, max_bytes)
        if kind == "file":
            suffix = await asyncio.to_thread(_file_suffix, path)
            renamed = self.originals / (key + suffix)
            if path != renamed:
                if renamed.exists() or renamed.is_symlink():
                    await asyncio.to_thread(self._validate_original, renamed, max_bytes)
                await asyncio.to_thread(path.replace, renamed)
                path = renamed
        return "file", {"path": str(path), "source_type": "wechat"}
