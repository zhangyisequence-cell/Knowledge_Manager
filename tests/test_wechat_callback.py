"""Real encrypted HTTP callbacks with a real SQLite store; no account/network access."""

import base64
import hashlib
import importlib
import sqlite3
from urllib.parse import urlencode
from xml.sax.saxutils import escape

import pytest
from backend.wechat.store import WeChatStore
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient

NOW = 1800000000
PUBLIC_KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
PUBLIC_TOKEN = "QDG6eK"
CORP_ID = "wx5823bf96d3bd56c7"
ACCOUNT = "kf-authorized"
DEFAULT_SETTINGS = {
    "corp_id": CORP_ID,
    "token": PUBLIC_TOKEN,
    "encoding_aes_key": PUBLIC_KEY,
    "allowed_accounts": {ACCOUNT},
}


@pytest.fixture
def store(tmp_path):
    value = WeChatStore(tmp_path / "callback.sqlite")
    value.initialize()
    return value


def client_for(store, monkeypatch):
    # Import inside the test call so a missing implementation is a test failure.
    callback = importlib.import_module("backend.wechat.callback")
    monkeypatch.setattr(callback.time, "time", lambda: NOW)
    settings = callback.CallbackSettings(**DEFAULT_SETTINGS)
    return TestClient(callback.create_callback_app(store, settings))


def encrypted_request(plaintext, *, timestamp=NOW, receiver=CORP_ID):
    """Build actual encrypted wire requests independently of the callback module."""
    message = plaintext.encode("utf-8")
    frame = (
        bytes(range(16)) + len(message).to_bytes(4, "big") + message + receiver.encode()
    )
    padder = padding.PKCS7(256).padder()
    padded = padder.update(frame) + padder.finalize()
    key = base64.b64decode(PUBLIC_KEY + "=")
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = base64.b64encode(
        encryptor.update(padded) + encryptor.finalize()
    ).decode()
    nonce = "1372623149"
    timestamp = str(timestamp)
    signature = hashlib.sha1(
        "".join(sorted([PUBLIC_TOKEN, timestamp, nonce, encrypted])).encode()
    ).hexdigest()
    return encrypted, {
        "msg_signature": signature,
        "timestamp": timestamp,
        "nonce": nonce,
    }


def event_xml(**overrides):
    values = {
        "ToUserName": CORP_ID,
        "CreateTime": str(NOW),
        "MsgType": "event",
        "Event": "kf_msg_or_event",
        "Token": "synthetic-sync-token",
        "OpenKfId": ACCOUNT,
    }
    values.update(overrides)
    return (
        "<xml>"
        + "".join(f"<{key}>{escape(value)}</{key}>" for key, value in values.items())
        + "</xml>"
    )


def post_event(client, *, timestamp=NOW, **overrides):
    encrypted, query = encrypted_request(event_xml(**overrides), timestamp=timestamp)
    return client.post(
        "/wechat/callback",
        params=query,
        content=f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>",
        headers={"Content-Type": "application/xml"},
    )


def test_get_returns_exact_decrypted_challenge_without_creating_work(
    store, monkeypatch
):
    client = client_for(store, monkeypatch)
    encrypted, query = encrypted_request("1234567890")
    response = client.get("/wechat/callback", params={**query, "echostr": encrypted})
    assert response.status_code == 200
    assert response.content == b"1234567890"
    assert response.headers["content-type"].startswith("text/plain")
    assert store.accounts_to_sync() == []


def test_post_acknowledges_only_after_notification_survives_reopen(store, monkeypatch):
    response = post_event(client_for(store, monkeypatch))
    assert response.status_code == 200 and response.content == b"success"
    reopened = WeChatStore(store.path)
    assert reopened.accounts_to_sync()[0]["open_kfid"] == ACCOUNT
    assert reopened.account(ACCOUNT)["token"] == "synthetic-sync-token"


def test_duplicate_notifications_remain_one_pending_account(store, monkeypatch):
    client = client_for(store, monkeypatch)
    assert post_event(client).status_code == 200
    assert post_event(client).status_code == 200
    assert len(store.accounts_to_sync()) == 1
    assert store.pending_messages() == []
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_invalid_signature_cannot_verify_or_queue(method, store, monkeypatch):
    client = client_for(store, monkeypatch)
    encrypted, query = encrypted_request(event_xml())
    query["msg_signature"] = "0" * 40
    response = client.request(
        method,
        "/wechat/callback",
        params={**query, "echostr": encrypted},
        content=f"<xml><Encrypt>{encrypted}</Encrypt></xml>",
    )
    assert response.status_code == 403
    assert store.accounts_to_sync() == []


@pytest.mark.parametrize("offset", [-601, 601])
def test_expired_or_far_future_notification_is_rejected(offset, store, monkeypatch):
    response = post_event(client_for(store, monkeypatch), timestamp=NOW + offset)
    assert response.status_code == 403
    assert store.accounts_to_sync() == []


@pytest.mark.parametrize("offset", [-600, 600])
def test_timestamp_window_includes_ten_minute_boundary(offset, store, monkeypatch):
    assert (
        post_event(client_for(store, monkeypatch), timestamp=NOW + offset).status_code
        == 200
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"ToUserName": "other-corp"},
        {"MsgType": "text"},
        {"Event": "other_event"},
        {"OpenKfId": "unlisted-kf"},
        {"Token": ""},
        {"Token": "   "},
        {"Token": "x" * 2049},
    ],
)
def test_only_expected_event_for_allowed_account_can_queue(
    overrides, store, monkeypatch
):
    response = post_event(client_for(store, monkeypatch), **overrides)
    assert response.status_code == 403
    assert store.accounts_to_sync() == []


@pytest.mark.parametrize(
    "query_change", [{"timestamp": "NaN"}, {"nonce": ""}, {"msg_signature": ""}]
)
def test_missing_or_malformed_query_is_rejected(query_change, store, monkeypatch):
    client = client_for(store, monkeypatch)
    encrypted, query = encrypted_request(event_xml())
    query.update(query_change)
    response = client.post(
        "/wechat/callback",
        params=query,
        content=f"<xml><Encrypt>{encrypted}</Encrypt></xml>",
    )
    assert response.status_code == 403
    assert store.accounts_to_sync() == []


def test_duplicate_signed_query_parameter_is_rejected(store, monkeypatch):
    client = client_for(store, monkeypatch)
    encrypted, query = encrypted_request(event_xml())
    response = client.post(
        "/wechat/callback",
        params=[*query.items(), ("nonce", query["nonce"])],
        content=f"<xml><Encrypt>{encrypted}</Encrypt></xml>",
    )
    assert response.status_code == 403
    assert store.accounts_to_sync() == []


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1"}])
def test_oversize_body_is_rejected_even_when_content_length_lies(
    headers, store, monkeypatch
):
    client = client_for(store, monkeypatch)
    _, query = encrypted_request(event_xml())
    response = client.post(
        "/wechat/callback",
        params=query,
        content=b"x" * (96 * 1024 + 1),
        headers=headers,
    )
    assert response.status_code == 413
    assert store.accounts_to_sync() == []


def test_store_failure_does_not_acknowledge_delivery(store, monkeypatch, tmp_path):
    broken_path = tmp_path / "broken.sqlite"
    broken_path.write_bytes(b"not a database")
    response = post_event(client_for(WeChatStore(broken_path), monkeypatch))
    assert response.status_code == 503
    assert response.content != b"success"
    assert b"synthetic-sync-token" not in response.content


@pytest.mark.asyncio
async def test_chunked_stream_stops_reading_as_soon_as_size_limit_is_crossed(
    store, monkeypatch
):
    app = client_for(store, monkeypatch).app
    _, query = encrypted_request(event_xml())
    chunks = iter([b"x" * 32768, b"x" * 32768, b"x" * 32768, b"x"])
    responses = []

    async def receive():
        try:
            chunk = next(chunks)
        except StopIteration:
            pytest.fail(
                "Callback consumed more of the stream after its byte limit was exceeded"
            )
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message):
        responses.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/wechat/callback",
        "root_path": "",
        "raw_path": b"/wechat/callback",
        "query_string": urlencode(query).encode(),
        "headers": [],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
    }
    await app(scope, receive, send)
    assert responses[0]["status"] == 413
    assert store.accounts_to_sync() == []


@pytest.mark.parametrize(
    "path", ["/", "/docs", "/redoc", "/openapi.json", "/api/jobs", "/api/config"]
)
def test_callback_app_does_not_expose_management_routes(path, store, monkeypatch):
    assert client_for(store, monkeypatch).get(path).status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"corp_id": ""},
        {"token": ""},
        {"encoding_aes_key": ""},
        {"allowed_accounts": set()},
        {"allowed_accounts": {""}},
        {"allowed_accounts": ACCOUNT},
    ],
)
def test_incomplete_callback_configuration_cannot_enable_app(change, store):
    callback = importlib.import_module("backend.wechat.callback")
    with pytest.raises(ValueError):
        settings = callback.CallbackSettings(**{**DEFAULT_SETTINGS, **change})
        callback.create_callback_app(store, settings)
