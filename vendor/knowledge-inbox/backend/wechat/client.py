"""Official customer-service API transport; credentials never appear in raised errors."""
import asyncio
import time

import httpx

API_ORIGIN = 'https://qyapi.weixin.qq.com'


class WeChatAPIError(RuntimeError):
    pass


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
            response = await self._client.request(method, API_ORIGIN + path,
                                                 follow_redirects=False, timeout=30, **kwargs)
            response.raise_for_status()
            if len(response.content) > 16 * 1024 * 1024:
                raise WeChatAPIError('WeChat API response exceeds size limit')
            result = response.json()
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
            raise WeChatAPIError(f'WeChat API error code {label}')

    async def sync_messages(self, account: str, token: str, cursor: str) -> dict:
        body = {'open_kfid': account, 'token': token, 'cursor': cursor,
                'limit': 1000, 'voice_format': 0}
        for attempt in range(2):
            access = await self._token()
            response = await self._request('POST', '/cgi-bin/kf/sync_msg',
                                           params={'access_token': access}, json=body)
            code = response.get('errcode')
            if type(code) is int and code in {40014, 42001} and attempt == 0:
                self._access_token = ''
                continue
            self._check_error(response)
            return response
        raise WeChatAPIError('WeChat token refresh failed')
