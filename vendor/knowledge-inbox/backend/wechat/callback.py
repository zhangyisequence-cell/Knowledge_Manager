"""An isolated callback app; callers provide explicit settings and an initialized store."""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from backend.wechat.crypto import CallbackCryptoError, WeChatCallbackCrypto
from backend.wechat.store import WeChatStore

MAX_REQUEST_BYTES = 96 * 1024
TIMESTAMP_WINDOW_SECONDS = 10 * 60


def _nonempty(value: str, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= max_length
        and not any(ord(char) < 32 for char in value)
    )


@dataclass(frozen=True)
class CallbackSettings:
    """No implicit credentials or allow-all account list; secrets are excluded from repr."""

    corp_id: str
    token: str = field(repr=False)
    encoding_aes_key: str = field(repr=False)
    allowed_accounts: frozenset[str]

    def __post_init__(self):
        if not (
            _nonempty(self.corp_id, 256)
            and _nonempty(self.token, 256)
            and _nonempty(self.encoding_aes_key, 43)
            and isinstance(self.allowed_accounts, (set, frozenset, list, tuple))
            and self.allowed_accounts
            and all(_nonempty(account, 512) for account in self.allowed_accounts)
        ):
            raise ValueError(
                "Explicit WeChat callback credentials and allowed accounts are required"
            )
        object.__setattr__(self, "allowed_accounts", frozenset(self.allowed_accounts))


def _query(request: Request, name: str) -> str:
    values = request.query_params.getlist(name)
    if len(values) != 1 or not values[0]:
        raise HTTPException(status_code=403, detail="Invalid callback")
    return values[0]


def _signature_parameters(request: Request) -> tuple[str, str, str]:
    signature = _query(request, "msg_signature")
    timestamp = _query(request, "timestamp")
    nonce = _query(request, "nonce")
    if (
        not re.fullmatch(r"[0-9]{1,12}", timestamp)
        or abs(time.time() - int(timestamp)) > TIMESTAMP_WINDOW_SECONDS
    ):
        raise HTTPException(status_code=403, detail="Invalid callback")
    return signature, timestamp, nonce


async def _body(request: Request) -> bytes:
    # Check both declared and actual bytes: chunked bodies or false Content-Length
    # must not bypass the memory bound. Stop reading immediately on overflow.
    length = request.headers.get("content-length")
    if length is not None:
        if not re.fullmatch(r"[0-9]{1,12}", length):
            raise HTTPException(status_code=400, detail="Invalid request length")
        if int(length) > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Callback body too large")
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Callback body too large")
            body.extend(chunk)
    except ClientDisconnect:
        raise HTTPException(status_code=400, detail="Incomplete callback") from None
    return bytes(body)


def create_callback_app(store: WeChatStore, settings: CallbackSettings) -> FastAPI:
    """Build only GET/POST /wechat/callback; never mount ingestion/admin routes.

    The caller initializes the store before serving. Callback validation performs
    no external API calls. Acknowledgment follows the durable notify transaction.
    """
    crypto = WeChatCallbackCrypto(settings.token, settings.encoding_aes_key, settings.corp_id)
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/wechat/callback", response_class=PlainTextResponse)
    async def verify_url(request: Request) -> PlainTextResponse:
        signature, timestamp, nonce = _signature_parameters(request)
        encrypted = _query(request, "echostr")
        try:
            challenge = crypto.decrypt_challenge(encrypted, signature, timestamp, nonce)
        except CallbackCryptoError:
            raise HTTPException(status_code=403, detail="Invalid callback") from None
        return PlainTextResponse(challenge)

    @app.post("/wechat/callback", response_class=PlainTextResponse)
    async def notification(request: Request) -> PlainTextResponse:
        signature, timestamp, nonce = _signature_parameters(request)
        body = await _body(request)
        try:
            fields = crypto.decrypt_xml(body, signature, timestamp, nonce)
        except CallbackCryptoError:
            raise HTTPException(status_code=403, detail="Invalid callback") from None
        account = fields.get("OpenKfId", "")
        token = fields.get("Token", "")
        if (
            fields.get("ToUserName") != settings.corp_id
            or fields.get("MsgType") != "event"
            or fields.get("Event") != "kf_msg_or_event"
            or account not in settings.allowed_accounts
            or not _nonempty(token, 2048)
        ):
            raise HTTPException(status_code=403, detail="Invalid callback")
        try:
            await run_in_threadpool(store.notify, account, token)
        except (sqlite3.Error, OSError):
            raise HTTPException(
                status_code=503, detail="Callback persistence unavailable"
            ) from None
        return PlainTextResponse("success")

    return app
