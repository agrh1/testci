# bot/utils/polling.py
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from bot.utils.sd_state import make_ids_snapshot_hash, normalize_tasks_for_message
from bot.utils.sd_web_client import SdOpenResult, SdWebClient
from bot.utils.state_store import StateStore


@dataclass
class PollingState:
    runs: int = 0
    failures: int = 0
    consecutive_failures: int = 0

    last_run_ts: Optional[float] = None
    last_success_ts: Optional[float] = None

    last_error: Optional[str] = None
    last_duration_ms: Optional[int] = None

    last_sent_snapshot: Optional[str] = None
    last_sent_ids: Optional[list[int]] = None

    last_sent_count: Optional[int] = None
    last_sent_at: Optional[float] = None

    last_notify_attempt_at: Optional[float] = None
    notify_skipped_rate_limit: int = 0

    last_calculated_count: Optional[int] = None
    last_calculated_at: Optional[float] = None


def _fmt_state_message(*, normalized_items: list[dict[str, object]], max_items_in_message: int) -> str:
    now_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))

    if len(normalized_items) == 0:
        return f"📌 Открытых заявок нет ✅ — {now_s}"

    shown = normalized_items[:max_items_in_message]
    lines = [f"📌 Открытые заявки ({len(normalized_items)}) — {now_s}"]
    for t in shown:
        lines.append(f"- #{t['Id']}: {t['Name']}")

    rest = len(normalized_items) - len(shown)
    if rest > 0:
        lines.append(f"… и ещё {rest} заявок")

    return "\n".join(lines)


def load_polling_state_from_store(state: PollingState, store: StateStore, key: str) -> None:
    data = store.get_json(key)
    if not data:
        return

    state.last_sent_snapshot = data.get("last_sent_snapshot")
    state.last_sent_ids = data.get("last_sent_ids")
    state.last_sent_count = data.get("last_sent_count")
    state.last_sent_at = data.get("last_sent_at")

    state.last_notify_attempt_at = data.get("last_notify_attempt_at")
    state.notify_skipped_rate_limit = int(data.get("notify_skipped_rate_limit", 0))


def save_polling_state_to_store(state: PollingState, store: StateStore, key: str) -> None:
    payload = {
        "last_sent_snapshot": state.last_sent_snapshot,
        "last_sent_ids": state.last_sent_ids,
        "last_sent_count": state.last_sent_count,
        "last_sent_at": state.last_sent_at,
        "last_notify_attempt_at": state.last_notify_attempt_at,
        "notify_skipped_rate_limit": state.notify_skipped_rate_limit,
    }
    store.set_json(key, payload)


async def polling_open_queue_loop(
    *,
    state: PollingState,
    stop_event: asyncio.Event,
    sd_web_client: SdWebClient,
    # Основное уведомление (список) — только при изменении состава очереди
    notify_main: Callable[[list[dict], str], Awaitable[None]],
    # Эскалация — дополнительно, может сработать без изменения очереди
    notify_escalation: Optional[Callable[[list[dict], str], Awaitable[None]]] = None,
    # Функция, которая возвращает "тикеты для эскалации" на текущем цикле
    get_escalations: Optional[Callable[[list[dict]], list[dict]]] = None,
    base_interval_s: float = 30.0,
    max_backoff_s: float = 300.0,
    min_notify_interval_s: float = 60.0,
    max_items_in_message: int = 10,
    store: Optional[StateStore] = None,
    store_key: str = "bot:polling_state",
) -> None:
    interval_s = base_interval_s

    if store is not None:
        load_polling_state_from_store(state, store, store_key)

    while not stop_event.is_set():
        state.last_run_ts = time.time()
        state.runs += 1
        t0 = time.perf_counter()

        # шаг 24: ping чтобы видеть падение/восстановление Redis
        if store is not None:
            ping_fn = getattr(store, "ping", None)
            if callable(ping_fn):
                try:
                    ping_fn()
                except Exception:
                    pass

        try:
            res: SdOpenResult = await sd_web_client.get_open(limit=200)
            state.last_duration_ms = int((time.perf_counter() - t0) * 1000)

            if not res.ok:
                state.failures += 1
                state.consecutive_failures += 1
                state.last_error = res.error or "sd_open_error"
                interval_s = min(max_backoff_s, max(base_interval_s, interval_s * 2))
            else:
                state.last_success_ts = time.time()
                state.last_error = None
                state.consecutive_failures = 0
                interval_s = base_interval_s

                # --- 1) Эскалации (не зависят от изменения снэпшота) ---
                if notify_escalation is not None and get_escalations is not None:
                    escalations = get_escalations(res.items)
                    if escalations:
                        # Текст эскалации формируем отдельно (в bot.py), тут только вызываем callback
                        await notify_escalation(escalations, "ESCALATION")

                # --- 2) Основной список (как раньше — только при изменении) ---
                snapshot_hash, ids = make_ids_snapshot_hash(res.items)

                state.last_calculated_count = len(ids)
                state.last_calculated_at = time.time()

                changed = (state.last_sent_snapshot is None) or (snapshot_hash != state.last_sent_snapshot)

                if changed:
                    normalized = normalize_tasks_for_message(res.items)
                    text = _fmt_state_message(
                        normalized_items=normalized,
                        max_items_in_message=max_items_in_message,
                    )

                    now = time.time()
                    if (
                        state.last_notify_attempt_at is not None
                        and (now - state.last_notify_attempt_at) < min_notify_interval_s
                    ):
                        state.notify_skipped_rate_limit += 1
                    else:
                        await notify_main(res.items, text)
                        state.last_notify_attempt_at = now

                        state.last_sent_snapshot = snapshot_hash
                        state.last_sent_ids = ids
                        state.last_sent_count = len(ids)
                        state.last_sent_at = time.time()

                        if store is not None:
                            save_polling_state_to_store(state, store, store_key)

        except Exception as e:
            state.last_duration_ms = int((time.perf_counter() - t0) * 1000)
            state.failures += 1
            state.consecutive_failures += 1
            state.last_error = str(e)
            interval_s = min(max_backoff_s, max(base_interval_s, interval_s * 2))

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
