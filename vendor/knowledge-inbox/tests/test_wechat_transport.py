from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from backend.wechat.client import WeChatAPI, WeChatAPIError


class CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.yielded = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk


def make_api(handler) -> tuple[WeChatAPI, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WeChatAPI("corp-id", "secret-value", client=client), client


def test_send_text_uses_official_payload_and_reuses_msgid() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"access_token": "access-value", "expires_in": 7200})
        assert request.url.path == "/cgi-bin/kf/send_msg"
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    api, client = make_api(handler)
    try:
        result = asyncio.run(
            api.send_text("kf-account", "external-user", "stable-message-id", "你好，世界")
        )
    finally:
        asyncio.run(client.aclose())

    assert result == {"errcode": 0, "errmsg": "ok"}
    send_request = requests[-1]
    assert send_request.url.params["access_token"] == "access-value"
    assert json.loads(send_request.content) == {
        "open_kfid": "kf-account",
        "touser": "external-user",
        "msgid": "stable-message-id",
        "msgtype": "text",
        "text": {"content": "你好，世界"},
    }


def test_send_text_rejects_content_over_2048_utf8_bytes_without_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    api, client = make_api(handler)
    try:
        try:
            asyncio.run(api.send_text("kf", "user", "message-id", "界" * 683))
        except ValueError as error:
            assert "2048 UTF-8 bytes" in str(error)
        else:
            raise AssertionError("expected oversized content to fail")
    finally:
        asyncio.run(client.aclose())

    assert calls == 0


def test_send_text_refreshes_an_expired_token_once() -> None:
    token_count = 0
    sent_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_count
        if request.url.path == "/cgi-bin/gettoken":
            token_count += 1
            return httpx.Response(
                200,
                json={"access_token": f"access-{token_count}", "expires_in": 7200},
            )
        sent_tokens.append(request.url.params["access_token"])
        if len(sent_tokens) == 1:
            return httpx.Response(200, json={"errcode": 42001, "errmsg": "expired"})
        return httpx.Response(200, json={"errcode": 0})

    api, client = make_api(handler)
    try:
        asyncio.run(api.send_text("kf", "user", "message-id", "hello"))
    finally:
        asyncio.run(client.aclose())

    assert sent_tokens == ["access-1", "access-2"]


def test_json_request_stops_reading_when_response_exceeds_limit() -> None:
    stream = CountingStream([b"x" * (1024 * 1024)] * 18)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    api, client = make_api(handler)
    try:
        try:
            asyncio.run(api._request("GET", "/oversized"))
        except WeChatAPIError as error:
            assert "size limit" in str(error)
        else:
            raise AssertionError("expected oversized response to fail")
    finally:
        asyncio.run(client.aclose())

    assert stream.yielded == 17


def test_download_media_streams_to_an_atomic_destination(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "voice.amr"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"access_token": "access-value", "expires_in": 7200})
        assert not destination.exists()
        return httpx.Response(
            200,
            headers={"content-type": "audio/amr"},
            content=b"media-content",
        )

    api, client = make_api(handler)
    try:
        saved = asyncio.run(api.download_media("media-id", destination, max_bytes=32))
    finally:
        asyncio.run(client.aclose())

    assert saved == destination
    assert destination.read_bytes() == b"media-content"
    assert requests[-1].url.path == "/cgi-bin/media/get"
    assert dict(requests[-1].url.params) == {
        "access_token": "access-value",
        "media_id": "media-id",
    }
    assert list(destination.parent.glob("*.part")) == []


def test_download_media_removes_partial_file_when_stream_exceeds_limit(tmp_path: Path) -> None:
    destination = tmp_path / "large.bin"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"access_token": "access-value", "expires_in": 7200})
        return httpx.Response(200, content=b"0123456789")

    api, client = make_api(handler)
    try:
        try:
            asyncio.run(api.download_media("media-id", destination, max_bytes=5))
        except WeChatAPIError as error:
            assert "size limit" in str(error)
        else:
            raise AssertionError("expected oversized media to fail")
    finally:
        asyncio.run(client.aclose())

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_media_exposes_json_error_code_without_secrets(tmp_path: Path) -> None:
    destination = tmp_path / "missing.bin"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"access_token": "secret-access", "expires_in": 7200})
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"errcode": 40007, "errmsg": "invalid media secret-access"},
        )

    api, client = make_api(handler)
    try:
        try:
            asyncio.run(api.download_media("private-media-id", destination, max_bytes=1024))
        except WeChatAPIError as error:
            assert error.code == 40007
            assert "40007" in str(error)
            assert "secret-access" not in str(error)
            assert "private-media-id" not in str(error)
            assert "https://" not in str(error)
        else:
            raise AssertionError("expected API error")
    finally:
        asyncio.run(client.aclose())

    assert not destination.exists()


def test_download_media_detects_api_error_with_text_plain_content_type(tmp_path: Path) -> None:
    destination = tmp_path / "missing.bin"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"access_token": "access-value", "expires_in": 7200})
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b'{"errcode":40007,"errmsg":"invalid media"}',
        )

    api, client = make_api(handler)
    try:
        try:
            asyncio.run(api.download_media("media-id", destination, max_bytes=1024))
        except WeChatAPIError as error:
            assert error.code == 40007
        else:
            raise AssertionError("expected API error")
    finally:
        asyncio.run(client.aclose())

    assert not destination.exists()


def test_download_media_keeps_real_json_file_without_errcode(tmp_path: Path) -> None:
    destination = tmp_path / "document.json"
    payload = b'{"title":"saved document","items":[1,2]}'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"access_token": "access-value", "expires_in": 7200})
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=payload,
        )

    api, client = make_api(handler)
    try:
        saved = asyncio.run(api.download_media("media-id", destination, max_bytes=1024))
    finally:
        asyncio.run(client.aclose())

    assert saved.read_bytes() == payload


def test_download_media_rejects_empty_success_payload(tmp_path: Path) -> None:
    destination = tmp_path / "empty.bin"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"access_token": "access-value", "expires_in": 7200})
        return httpx.Response(200, content=b"")

    api, client = make_api(handler)
    try:
        try:
            asyncio.run(api.download_media("media-id", destination, max_bytes=1024))
        except WeChatAPIError as error:
            assert "empty media" in str(error)
        else:
            raise AssertionError("expected empty media to fail")
    finally:
        asyncio.run(client.aclose())

    assert not destination.exists()


def test_download_media_refreshes_expired_token_once(tmp_path: Path) -> None:
    token_count = 0
    media_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_count
        if request.url.path == "/cgi-bin/gettoken":
            token_count += 1
            return httpx.Response(
                200,
                json={"access_token": f"access-{token_count}", "expires_in": 7200},
            )
        media_tokens.append(request.url.params["access_token"])
        if len(media_tokens) == 1:
            return httpx.Response(200, json={"errcode": 40014, "errmsg": "invalid token"})
        return httpx.Response(200, content=b"ok")

    api, client = make_api(handler)
    try:
        saved = asyncio.run(api.download_media("media-id", tmp_path / "media.bin", max_bytes=2))
    finally:
        asyncio.run(client.aclose())

    assert saved.read_bytes() == b"ok"
    assert media_tokens == ["access-1", "access-2"]


def test_download_media_does_not_follow_redirects(tmp_path: Path) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(200, json={"access_token": "access-value", "expires_in": 7200})
        return httpx.Response(302, headers={"location": "https://untrusted.example/media"})

    api, client = make_api(handler)
    try:
        try:
            asyncio.run(api.download_media("media-id", tmp_path / "media.bin", max_bytes=10))
        except WeChatAPIError as error:
            assert "network or response error" in str(error)
            assert "untrusted.example" not in str(error)
        else:
            raise AssertionError("expected redirect response to fail")
    finally:
        asyncio.run(client.aclose())

    assert paths == ["/cgi-bin/gettoken", "/cgi-bin/media/get"]
    assert list(tmp_path.iterdir()) == []


def test_api_error_carries_numeric_code_without_api_message() -> None:
    try:
        WeChatAPI._check_error({"errcode": 50001, "errmsg": "contains a secret"})
    except WeChatAPIError as error:
        assert error.code == 50001
        assert str(error) == "WeChat API error code 50001"
    else:
        raise AssertionError("expected API error")
