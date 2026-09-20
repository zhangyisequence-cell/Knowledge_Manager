"""Explicit server-only environment configuration and isolated public app factory."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx

from backend.wechat.callback import CallbackSettings, create_callback_app
from backend.wechat.client import WeChatAPI
from backend.wechat.crypto import WeChatCallbackCrypto
from backend.wechat.store import WeChatStore
from backend.wechat.worker import WeChatWorker


@dataclass(frozen=True)
class ServerSettings:
    callback: CallbackSettings
    secret: str = field(repr=False)
    allowed_senders: frozenset[str]


def settings_from_env() -> ServerSettings | None:
    if os.getenv("WECHAT_ENABLED", "").lower() != "true":
        return None
    try:
        callback = CallbackSettings(
            corp_id=os.environ["WECHAT_CORP_ID"],
            token=os.environ["WECHAT_CALLBACK_TOKEN"],
            encoding_aes_key=os.environ["WECHAT_ENCODING_AES_KEY"],
            allowed_accounts=frozenset(
                v.strip() for v in os.environ["WECHAT_ALLOWED_ACCOUNTS"].split(",") if v.strip()
            ),
        )
        secret = os.environ["WECHAT_SECRET"]
        senders = frozenset(
            v.strip() for v in os.environ["WECHAT_ALLOWED_SENDERS"].split(",") if v.strip()
        )
        if (
            not secret.strip()
            or not senders
            or any(len(v) > 512 or any(ord(c) < 32 for c in v) for v in senders)
        ):
            raise ValueError()
        WeChatCallbackCrypto(callback.token, callback.encoding_aes_key, callback.corp_id)
    except (KeyError, ValueError):
        raise ValueError(
            "Enabled WeChat server requires complete credentials and explicit account/sender allowlists"
        ) from None
    return ServerSettings(callback, secret, senders)


def create_public_app():
    settings = settings_from_env()
    if settings is None:
        raise ValueError("WeChat callback is disabled")
    from backend.config import get_config

    store = WeChatStore(get_config().database_path)
    store.initialize()
    return create_callback_app(store, settings.callback)


@asynccontextmanager
async def running_worker(config, ingestion):
    settings = settings_from_env()
    if settings is None:
        yield None
        return
    if not config.storage_configured:
        raise ValueError("WeChat server requires configured knowledge storage")
    store = WeChatStore(config.database_path)
    store.initialize()
    async with httpx.AsyncClient(trust_env=False) as client:
        api = WeChatAPI(settings.callback.corp_id, settings.secret, client=client)
        worker = WeChatWorker(
            store,
            api,
            ingestion,
            config.data_dir / "originals" / "wechat",
            settings.callback.allowed_accounts,
            settings.allowed_senders,
        )
        await worker.start()
        try:
            yield worker
        finally:
            await worker.stop()
