"""
Фоновый воркер для обработки eventlog.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from bot.utils.eventlog import get_item, get_last_item, message_important_checker, parse_event
from bot.utils.state_store import StateStore

logger = logging.getLogger(__name__)

EVENTLOG_STATE_KEY = "bot:eventlog"


async def eventlog_loop(
    *,
    stop_event: asyncio.Event,
    notify_eventlog: Callable[[str, list[dict]], Awaitable[None]],
    store: Optional[StateStore],
    login: str,
    password: str,
    base_url: str,
    poll_interval_s: int = 600,
    keepalive_every: int = 48,
    start_event_id: int = 0,
) -> None:
    if not login or not password or not base_url:
        logger.warning("eventlog disabled: missing credentials or base_url")
        return

    last_event_id = _load_last_event_id(store)
    if last_event_id is None or last_event_id <= 0:
        last_event_id = await _bootstrap_event_id(
            login=login,
            password=password,
            base_url=base_url,
            start_event_id=start_event_id,
        )
        _save_last_event_id(store, last_event_id)

    timer = 0

    while not stop_event.is_set():
        next_id = last_event_id + 1
        res = await asyncio.to_thread(get_item, next_id, login, password, base_url)
        if res is None:
            timer += 1
            if keepalive_every > 0 and timer >= keepalive_every:
                last_item = await asyncio.to_thread(get_last_item, login, password, base_url)
                await notify_eventlog(
                    f"I'm work, but no new messages.\nWaiting for event {next_id}\nlast item {last_item}",
                    [],
                )
                timer = 0

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
            except asyncio.TimeoutError:
                continue
            break

        timer = 0
        message = await asyncio.to_thread(parse_event, res)
        if message_important_checker(message):
            text = (
                f"{message.get('Дата', '')} {message.get('Тип', '')}\n"
                f"{message.get('Название', '')}\n"
                f"{message.get('Описание', '')[:300]}"
            )
            item = {"Name": f"{message.get('Тип', '')} {message.get('Название', '')}".strip()}
            await notify_eventlog(text, [item])

        last_event_id = next_id
        _save_last_event_id(store, last_event_id)


def _load_last_event_id(store: Optional[StateStore]) -> Optional[int]:
    if store is None:
        return None
    data = store.get_json(EVENTLOG_STATE_KEY)
    if not data:
        return None
    raw = data.get("last_event_id")
    try:
        return int(raw)
    except Exception:
        return None


def _save_last_event_id(store: Optional[StateStore], event_id: int) -> None:
    if store is None:
        return
    store.set_json(
        EVENTLOG_STATE_KEY,
        {"last_event_id": int(event_id), "updated_at": time.time()},
    )


async def _bootstrap_event_id(
    *,
    login: str,
    password: str,
    base_url: str,
    start_event_id: int,
) -> int:
    if start_event_id > 0:
        return start_event_id
    last_item = await asyncio.to_thread(get_last_item, login, password, base_url)
    try:
        return int(last_item)
    except Exception:
        return 0

