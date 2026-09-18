import asyncio
import json

import httpx
import pytest
from test_wechat_store import message, store_at


def test_sync_follows_empty_page_with_has_more_and_persists_before_cursor(tmp_path):
    from backend.wechat.client import WeChatAPI
    from backend.wechat.sync import synchronize_account

    store = store_at(tmp_path)
    store.notify('kf1', 'notify-token')
    requests = []

    def respond(request):
        if request.url.path == '/cgi-bin/gettoken':
            assert request.url.params['corpid'] == 'example-corp'
            return httpx.Response(200, json={'errcode': 0, 'access_token': 'access', 'expires_in': 7200})
        assert request.url.path == '/cgi-bin/kf/sync_msg'
        assert request.url.params['access_token'] == 'access'
        body = json.loads(request.content)
        assert body['open_kfid'] == 'kf1' and body['token'] == 'notify-token'
        assert body['voice_format'] == 0
        requests.append(body['cursor'])
        if body['cursor'] == '':
            return httpx.Response(200, json={'errcode': 0, 'next_cursor': 'c1', 'has_more': 1, 'msg_list': []})
        assert store.account('kf1')['cursor'] == 'c1'
        return httpx.Response(200, json={'errcode': 0, 'next_cursor': 'c2', 'has_more': 0,
                                        'msg_list': [message()]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            api = WeChatAPI('example-corp', 'synthetic-secret', client=client)
            await synchronize_account(store, api, store.account('kf1'), {'alice'})

    asyncio.run(run())
    assert requests == ['', 'c1']
    assert store.account('kf1')['cursor'] == 'c2'
    assert store.accounts_to_sync() == []
    assert store.pending_messages()[0]['msgid'] == 'm1'


def test_interrupted_sync_reopens_from_last_committed_page(tmp_path):
    from backend.wechat.client import WeChatAPI, WeChatAPIError
    from backend.wechat.sync import synchronize_account

    store = store_at(tmp_path)
    store.notify('kf1', 'token')

    def respond(request):
        if request.url.path.endswith('gettoken'):
            return httpx.Response(200, json={'access_token': 'access', 'expires_in': 7200})
        cursor = json.loads(request.content)['cursor']
        if cursor == '':
            return httpx.Response(200, json={'errcode': 0, 'next_cursor': 'saved', 'has_more': 1,
                                            'msg_list': [message()]})
        raise httpx.ReadTimeout('private token must not appear in raised error')

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            api = WeChatAPI('corp', 'secret', client=client)
            with pytest.raises(WeChatAPIError) as failure:
                await synchronize_account(store, api, store.account('kf1'), {'alice'})
            assert 'private token' not in str(failure.value)

    asyncio.run(run())
    reopened = store_at(tmp_path)
    assert reopened.account('kf1')['cursor'] == 'saved'
    assert len(reopened.pending_messages()) == 1
    assert reopened.accounts_to_sync()


@pytest.mark.parametrize('page', [
    {'errcode': 0, 'next_cursor': '', 'has_more': 1, 'msg_list': []},
    {'errcode': 0, 'next_cursor': 'c1', 'has_more': 0, 'msg_list': [message(), {}]},
])
def test_bad_page_cannot_advance_cursor_or_drop_pending_signal(tmp_path, page):
    from backend.wechat.client import WeChatAPI
    from backend.wechat.sync import synchronize_account

    store = store_at(tmp_path)
    store.notify('kf1', 'token')

    def respond(request):
        payload = {'access_token': 'access', 'expires_in': 7200} if request.url.path.endswith('gettoken') else page
        return httpx.Response(200, json=payload)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(ValueError):
                await synchronize_account(store, WeChatAPI('corp', 'secret', client=client),
                                          store.account('kf1'), {'alice'})

    asyncio.run(run())
    assert store.account('kf1')['cursor'] == ''
    assert store.pending_messages() == []
    assert store.accounts_to_sync()


def test_invalid_access_token_refreshes_once_without_skipping_page():
    from backend.wechat.client import WeChatAPI

    tokens = []
    pages = []

    def respond(request):
        if request.url.path.endswith('gettoken'):
            tokens.append('fresh' + str(len(tokens)))
            return httpx.Response(200, json={'access_token': tokens[-1], 'expires_in': 7200})
        pages.append(json.loads(request.content)['cursor'])
        if request.url.params['access_token'] == 'fresh0':
            return httpx.Response(200, json={'errcode': 42001, 'errmsg': 'expired'})
        return httpx.Response(200, json={'errcode': 0, 'next_cursor': 'done', 'has_more': 0, 'msg_list': []})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await WeChatAPI('corp', 'secret', client=client).sync_messages('kf1', 'token', 'same')
            assert result['next_cursor'] == 'done'

    asyncio.run(run())
    assert tokens == ['fresh0', 'fresh1'] and pages == ['same', 'same']
