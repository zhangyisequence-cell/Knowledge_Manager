"""Fetch durable pages without acknowledging a cursor ahead of stored messages."""
import asyncio

from backend.wechat.client import WeChatAPI
from backend.wechat.store import WeChatStore


async def synchronize_account(store: WeChatStore, api: WeChatAPI,
                              account: dict, allowed_senders: set[str]) -> None:
    cursor = account['cursor']
    # Yield back after bounded work. Pending stays set if further pages remain.
    for _ in range(100):
        page = await api.sync_messages(account['open_kfid'], account['token'], cursor)
        has_more, next_cursor, messages = page.get('has_more'), page.get('next_cursor'), page.get('msg_list')
        if type(has_more) is not int or has_more not in {0, 1}:
            raise ValueError('Invalid WeChat pagination flag')
        if has_more and (not next_cursor or next_cursor == cursor):
            raise ValueError('WeChat pagination did not advance')
        await asyncio.to_thread(store.save_page, account['open_kfid'], cursor,
                                next_cursor, messages, allowed_senders)
        cursor = next_cursor
        if not has_more:
            await asyncio.to_thread(store.finish_sync, account['open_kfid'], account['generation'], cursor)
            return
