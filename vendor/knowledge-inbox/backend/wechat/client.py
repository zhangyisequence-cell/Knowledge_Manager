"""Official customer-service API transport; credentials never appear in raised errors."""
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

import httpx

API_ORIGIN = 'https://qyapi.weixin.qq.com'


class WeChatAPIError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None):
        super().__init__(message)
        self.code = code


class WeChatAPI:
    def __init__(self, corp_id: str, secret: str, *, client: httpx.AsyncClient):
        if not corp_id.strip() or not secret.strip():
            raise ValueError('WeChat API credentials are required')
        self.corp_id = corp_id
        self._secret = secret
        self._client = client
        self._access_token = ''
        self._expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with self._client.stream(
                method,
                API_ORIGIN + path,
                follow_redirects=False,
                timeout=30,
                **kwargs,
            ) as response:
                response.raise_for_status()
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 16 * 1024 * 1024:
                        raise WeChatAPIError('WeChat API response exceeds size limit')
            result = json.loads(payload)
        except WeChatAPIError:
            raise
        except (httpx.HTTPError, ValueError):
            raise WeChatAPIError('WeChat API network or response error; retry is required') from None
        if not isinstance(result, dict):
            raise WeChatAPIError('WeChat API returned an invalid response')
        return result

    async def _token(self) -> str:
        async with self._token_lock:
            if self._access_token and time.monotonic() < self._expires_at:
                return self._access_token
            response = await self._request('GET', '/cgi-bin/gettoken',
                                           params={'corpid': self.corp_id, 'corpsecret': self._secret})
            self._check_error(response)
            token, ttl = response.get('access_token'), response.get('expires_in')
            if not isinstance(token, str) or not token or len(token) > 4096:
                raise WeChatAPIError('WeChat returned an invalid access token')
            if type(ttl) is not int or not 60 <= ttl <= 86400:
                raise WeChatAPIError('WeChat returned an invalid token lifetime')
            self._access_token = token
            self._expires_at = time.monotonic() + ttl - 60
            return token

    @staticmethod
    def _check_error(result: dict):
        code = result.get('errcode', 0)
        if type(code) is not int or code != 0:
            # Do not copy arbitrary API errmsg fields, which can contain request data.
            label = code if type(code) is int else 'invalid'
            numeric_code = code if type(code) is int else None
            raise WeChatAPIError(f'WeChat API error code {label}', code=numeric_code)

    def _expire_token(self):
        self._access_token = ''
        self._expires_at = 0.0

    async def sync_messages(self, account: str, token: str, cursor: str) -> dict:
        body = {'open_kfid': account, 'token': token, 'cursor': cursor,
                'limit': 1000, 'voice_format': 0}
        for attempt in range(2):
            access = await self._token()
            response = await self._request('POST', '/cgi-bin/kf/sync_msg',
                                           params={'access_token': access}, json=body)
            code = response.get('errcode')
            if type(code) is int and code in {40014, 42001} and attempt == 0:
                self._expire_token()
                continue
            self._check_error(response)
            return response
        raise WeChatAPIError('WeChat token refresh failed')

    async def send_text(
        self, account: str, recipient: str, send_id: str, content: str
    ) -> dict:
        if not all(isinstance(value, str) and value for value in (account, recipient, send_id)):
            raise ValueError('WeChat message identifiers are required')
        if not isinstance(content, str) or not content:
            raise ValueError('WeChat text content is required')
        if len(content.encode('utf-8')) > 2048:
            raise ValueError('WeChat text content exceeds 2048 UTF-8 bytes')

        body = {
            'open_kfid': account,
            'touser': recipient,
            'msgid': send_id,
            'msgtype': 'text',
            'text': {'content': content},
        }
        for attempt in range(2):
            access = await self._token()
            response = await self._request(
                'POST',
                '/cgi-bin/kf/send_msg',
                params={'access_token': access},
                json=body,
            )
            code = response.get('errcode')
            if type(code) is int and code in {40014, 42001} and attempt == 0:
                self._expire_token()
                continue
            self._check_error(response)
            return response
        raise WeChatAPIError('WeChat token refresh failed')

    async def download_media(
        self, media_id: str, destination: Path, *, max_bytes: int
    ) -> Path:
        if not isinstance(media_id, str) or not media_id:
            raise ValueError('WeChat media ID is required')
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError('Media size limit must be a positive integer')
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)

        for attempt in range(2):
            access = await self._token()
            result = await self._download_media_once(
                media_id,
                destination,
                max_bytes=max_bytes,
                access_token=access,
            )
            if isinstance(result, dict):
                code = result.get('errcode')
                if type(code) is int and code in {40014, 42001} and attempt == 0:
                    self._expire_token()
                    continue
                self._check_error(result)
                raise WeChatAPIError('WeChat API returned media metadata instead of media')
            return result
        raise WeChatAPIError('WeChat token refresh failed')

    async def _download_media_once(
        self,
        media_id: str,
        destination: Path,
        *,
        max_bytes: int,
        access_token: str,
    ) -> Path | dict:
        temporary_path: Path | None = None
        try:
            async with self._client.stream(
                'GET',
                API_ORIGIN + '/cgi-bin/media/get',
                params={'access_token': access_token, 'media_id': media_id},
                follow_redirects=False,
                timeout=30,
            ) as response:
                response.raise_for_status()
                declared_size = response.headers.get('content-length')
                if declared_size is not None:
                    try:
                        if int(declared_size) > max(max_bytes, 64 * 1024):
                            raise WeChatAPIError('WeChat media exceeds size limit')
                    except ValueError:
                        raise WeChatAPIError('WeChat API returned an invalid response') from None

                with tempfile.NamedTemporaryFile(
                    mode='wb',
                    dir=destination.parent,
                    prefix=f'.{destination.name}.',
                    suffix='.part',
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    received = 0
                    error_candidate = bytearray()
                    candidate_complete = True
                    async for chunk in response.aiter_bytes():
                        received += len(chunk)
                        temporary.write(chunk)
                        if candidate_complete:
                            if len(error_candidate) + len(chunk) <= 64 * 1024:
                                error_candidate.extend(chunk)
                            else:
                                candidate_complete = False
                                error_candidate.clear()
                        if received > max_bytes and not candidate_complete:
                            raise WeChatAPIError('WeChat media exceeds size limit')
                    if received == 0:
                        raise WeChatAPIError('WeChat API returned empty media')
                    temporary.flush()
                    os.fsync(temporary.fileno())

                if candidate_complete:
                    try:
                        candidate = json.loads(error_candidate)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        candidate = None
                    if (
                        isinstance(candidate, dict)
                        and type(candidate.get('errcode')) is int
                    ):
                        return candidate
                if received > max_bytes:
                    raise WeChatAPIError('WeChat media exceeds size limit')
                os.replace(temporary_path, destination)
                temporary_path = None
                return destination
        except WeChatAPIError:
            raise
        except httpx.HTTPError:
            raise WeChatAPIError(
                'WeChat API network or response error; retry is required'
            ) from None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
