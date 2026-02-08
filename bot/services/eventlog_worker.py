"""
Фоновый воркер для обработки eventlog.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

from bot.services.eventlog_filter_store import EventlogFilterStore, match_eventlog_filter
from bot.utils.eventlog import get_item, get_last_item, parse_event
from bot.utils.state_store import StateStore

logger = logging.getLogger("bot.eventlog")

EVENTLOG_STATE_KEY = "bot:eventlog"


async def eventlog_loop(
    *,
    stop_event: asyncio.Event,
    notify_eventlog: Callable[[str, list[dict]], Awaitable[None]],
    store: Optional[StateStore],
    filter_store: Optional[EventlogFilterStore],
    login: str,
    password: str,
    base_url: str,
    poll_interval_s: int = 600,
    keepalive_every: int = 48,
    start_event_id: int = 0,
    soft_catchup_after: int = 3,
) -> None:
    if not login or not password or not base_url:
        logger.warning("eventlog disabled: missing credentials or base_url")
        return

    last_event_id = _load_last_event_id(store)
    if last_event_id is None or last_event_id <= 0:
        logger.info("eventlog bootstrap: no last_event_id in store")
        last_event_id = await _bootstrap_event_id(
            login=login,
            password=password,
            base_url=base_url,
            start_event_id=start_event_id,
        )
        _save_last_event_id(store, last_event_id)
        logger.info("eventlog bootstrap: last_event_id=%s", last_event_id)
    else:
        logger.info("eventlog start: last_event_id=%s", last_event_id)

    timer = 0
    no_item_streak = 0

    while not stop_event.is_set():
        # Allow live updates of last_event_id via state store (e.g. /last_eventlog_id set <id>).
        stored_last_id = _load_last_event_id(store)
        if stored_last_id is not None and stored_last_id > last_event_id:
            logger.info("eventlog live update: last_event_id %s -> %s", last_event_id, stored_last_id)
            last_event_id = stored_last_id
            timer = 0

        next_id = last_event_id + 1
        logger.debug("eventlog poll: next_id=%s", next_id)
        try:
            res = await asyncio.to_thread(get_item, next_id, login, password, base_url)
        except Exception as e:
            logger.warning("eventlog get_item error: next_id=%s err=%s", next_id, e)
            res = None
        if res is None:
            no_item_streak += 1
            if soft_catchup_after > 0 and no_item_streak >= soft_catchup_after:
                try:
                    last_item = await asyncio.to_thread(get_last_item, login, password, base_url)
                    if last_item is not None:
                        last_item_id = int(last_item)
                        if last_item_id > next_id:
                            logger.info(
                                "eventlog soft catchup: next_id=%s -> last_item=%s",
                                next_id,
                                last_item_id,
                            )
                            last_event_id = last_item_id - 1
                            _save_last_event_id(store, last_event_id)
                            timer = 0
                            no_item_streak = 0
                            continue
                except Exception as e:
                    logger.warning("eventlog soft catchup error: %s", e)

            timer += 1
            logger.debug("eventlog no item: next_id=%s timer=%s", next_id, timer)
            if keepalive_every > 0 and timer >= keepalive_every:
                last_item = None
                try:
                    last_item = await asyncio.to_thread(get_last_item, login, password, base_url)
                except Exception as e:
                    logger.warning("eventlog get_last_item error: %s", e)
                try:
                    await notify_eventlog(
                        f"I'm work, but no new messages.\nWaiting for event {next_id}\nlast item {last_item}",
                        [],
                    )
                    logger.info("eventlog keepalive sent: next_id=%s last_item=%s", next_id, last_item)
                except Exception as e:
                    logger.warning("eventlog keepalive send error: %s", e)
                timer = 0

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_s)
            except asyncio.TimeoutError:
                continue
            break

        timer = 0
        no_item_streak = 0
        try:
            message = await asyncio.to_thread(parse_event, res)
        except Exception as e:
            logger.warning("eventlog parse error: next_id=%s err=%s", next_id, e)
            message = {}
        is_filtered = await _is_filtered(message, filter_store, next_id)
        if not is_filtered:
            text = (
                f"{message.get('Дата', '')} {message.get('Тип', '')}\n"
                f"{message.get('Название', '')}\n"
                f"{message.get('Описание', '')[:300]}"
            )
            item = {"Name": f"{message.get('Тип', '')} {message.get('Название', '')}".strip()}
            try:
                await notify_eventlog(text, [item])
                logger.info("eventlog notify sent: event_id=%s", next_id)
            except Exception as e:
                logger.warning("eventlog notify error: event_id=%s err=%s", next_id, e)
        else:
            logger.debug("eventlog filtered: event_id=%s", next_id)

        last_event_id = next_id
        _save_last_event_id(store, last_event_id)
        logger.debug("eventlog saved last_event_id=%s", last_event_id)


async def eventlog_poll_once(
    *,
    notify_eventlog: Callable[[str, list[dict]], Awaitable[None]],
    store: Optional[StateStore],
    filter_store: Optional[EventlogFilterStore],
    login: str,
    password: str,
    base_url: str,
    start_event_id: int = 0,
) -> dict[str, object]:
    """
    Выполняет одну итерацию обработки eventlog.
    Возвращает статус и детали для диагностических команд.
    """
    if not login or not password or not base_url:
        return {
            "ok": False,
            "status": "disabled",
            "reason": "missing credentials or base_url",
        }

    bootstrapped = False
    last_event_id = _load_last_event_id(store)
    if last_event_id is None or last_event_id <= 0:
        bootstrapped = True
        last_event_id = await _bootstrap_event_id(
            login=login,
            password=password,
            base_url=base_url,
            start_event_id=start_event_id,
        )
        if last_event_id and last_event_id > 0:
            _save_last_event_id(store, last_event_id)

    if not last_event_id or last_event_id <= 0:
        return {
            "ok": False,
            "status": "bootstrap_failed",
            "bootstrapped": bootstrapped,
        }

    next_id = last_event_id + 1
    try:
        res = await asyncio.to_thread(get_item, next_id, login, password, base_url)
    except Exception as e:
        return {
            "ok": False,
            "status": "get_item_error",
            "next_id": next_id,
            "error": str(e),
            "bootstrapped": bootstrapped,
        }

    if res is None:
        last_item = None
        try:
            last_item = await asyncio.to_thread(get_last_item, login, password, base_url)
        except Exception:
            last_item = None
        return {
            "ok": True,
            "status": "no_item",
            "next_id": next_id,
            "last_item": last_item,
            "bootstrapped": bootstrapped,
        }

    message: dict[str, str] = {}
    parse_error = None
    try:
        message = await asyncio.to_thread(parse_event, res)
    except Exception as e:
        parse_error = str(e)

    is_filtered = await _is_filtered(message, filter_store, next_id)
    if not is_filtered:
        text = (
            f"{message.get('Дата', '')} {message.get('Тип', '')}\n"
            f"{message.get('Название', '')}\n"
            f"{message.get('Описание', '')[:300]}"
        )
        item = {"Name": f"{message.get('Тип', '')} {message.get('Название', '')}".strip()}
        try:
            await notify_eventlog(text, [item])
        except Exception as e:
            _save_last_event_id(store, next_id)
            return {
                "ok": False,
                "status": "notify_error",
                "next_id": next_id,
                "error": str(e),
                "bootstrapped": bootstrapped,
            }
        status = "notified"
    else:
        status = "filtered"

    _save_last_event_id(store, next_id)
    return {
        "ok": True,
        "status": status,
        "next_id": next_id,
        "bootstrapped": bootstrapped,
        "parse_error": parse_error,
    }


async def _is_filtered(
    message: dict[str, str],
    filter_store: Optional[EventlogFilterStore],
    event_id: int,
) -> bool:
    if filter_store is None:
        return False
    try:
        filters = await filter_store.list_enabled()
    except Exception as e:
        logger.warning("eventlog filters load error: event_id=%s err=%s", event_id, e)
        return False
    if not filters:
        return False

    matched_ids: list[int] = []
    for f in filters:
        try:
            if match_eventlog_filter(f, message):
                matched_ids.append(f.filter_id)
        except Exception as e:
            logger.warning("eventlog filter match error: event_id=%s filter_id=%s err=%s", event_id, f.filter_id, e)

    if matched_ids:
        try:
            await filter_store.increment_hits(matched_ids)
        except Exception as e:
            logger.warning("eventlog filter hits update error: event_id=%s err=%s", event_id, e)
        logger.debug("eventlog filtered by ids: event_id=%s ids=%s", event_id, matched_ids)
        return True
    return False


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
