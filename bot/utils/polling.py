"""Polling очереди заявок и хранение состояния отправки уведомлений."""

# bot/utils/polling.py
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from bot.services.service_icon_store import ServiceIconStore
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

    # -----------------------------
    # Шаг 27A: наблюдаемость routing
    # -----------------------------
    tickets_without_destination_total: int = 0
    last_ticket_without_destination_at: Optional[float] = None

    # rate-limit для админ-алертов (чтобы не заспамить)
    last_admin_alert_at: Optional[float] = None
    admin_alerts_skipped_rate_limit: int = 0

    # 27D/27B: алерты по деградации и частым rollback
    last_web_alert_at: Optional[float] = None
    web_alerts_skipped_rate_limit: int = 0
    last_redis_alert_at: Optional[float] = None
    redis_alerts_skipped_rate_limit: int = 0
    last_rollback_alert_at: Optional[float] = None
    rollback_alerts_skipped_rate_limit: int = 0


def _fmt_state_message(
    *,
    normalized_items: list[dict[str, object]],
    max_items_in_message: int,
    service_icons: Optional[dict[int, str]] = None,
) -> str:
    return format_open_tasks_message(
        normalized_items=normalized_items,
        max_items_in_message=max_items_in_message,
        service_icons=service_icons,
    )


def format_open_tasks_message(
    *,
    normalized_items: list[dict[str, object]],
    max_items_in_message: int,
    service_icons: Optional[dict[int, str]] = None,
) -> str:
    if len(normalized_items) == 0:
        return "no one items in 'Open' status"

    shown = normalized_items[:max_items_in_message]
    lines: list[str] = []
    for idx, t in enumerate(shown, start=1):
        service_id = t.get("ServiceId")
        service_code = str(t.get("ServiceCode") or "").strip()
        service_name = str(t.get("ServiceName") or "").strip()
        header = " ".join([x for x in [service_code, service_name] if x]).strip()
        if not header and service_id is not None:
            header = f"ServiceId {service_id}"
        icon = ""
        if service_icons and isinstance(service_id, int):
            icon = service_icons.get(service_id, "")
        if icon:
            header = f"{icon}{header}"

        if header:
            lines.append(header)

        lines.append(str(t.get("Id")))

        created = str(t.get("Created") or "").strip()
        if created:
            lines.append(created.replace("T", " "))

        name = str(t.get("Name") or "").strip()
        if name:
            lines.append(name)

        creator = str(t.get("Creator") or "").strip()
        if creator:
            lines.append(creator)

        url = str(t.get("Url") or "").strip()
        if url:
            lines.append(url)

        if idx != len(shown):
            lines.append("")

    rest = len(normalized_items) - len(shown)
    if rest > 0:
        lines.append("")
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

    # 27A
    state.tickets_without_destination_total = int(data.get("tickets_without_destination_total", 0))
    state.last_ticket_without_destination_at = data.get("last_ticket_without_destination_at")
    state.last_admin_alert_at = data.get("last_admin_alert_at")
    state.admin_alerts_skipped_rate_limit = int(data.get("admin_alerts_skipped_rate_limit", 0))
    state.last_web_alert_at = data.get("last_web_alert_at")
    state.web_alerts_skipped_rate_limit = int(data.get("web_alerts_skipped_rate_limit", 0))
    state.last_redis_alert_at = data.get("last_redis_alert_at")
    state.redis_alerts_skipped_rate_limit = int(data.get("redis_alerts_skipped_rate_limit", 0))
    state.last_rollback_alert_at = data.get("last_rollback_alert_at")
    state.rollback_alerts_skipped_rate_limit = int(data.get("rollback_alerts_skipped_rate_limit", 0))


def save_polling_state_to_store(state: PollingState, store: StateStore, key: str) -> None:
    payload = {
        "last_sent_snapshot": state.last_sent_snapshot,
        "last_sent_ids": state.last_sent_ids,
        "last_sent_count": state.last_sent_count,
        "last_sent_at": state.last_sent_at,
        "last_notify_attempt_at": state.last_notify_attempt_at,
        "notify_skipped_rate_limit": state.notify_skipped_rate_limit,
        # 27A
        "tickets_without_destination_total": state.tickets_without_destination_total,
        "last_ticket_without_destination_at": state.last_ticket_without_destination_at,
        "last_admin_alert_at": state.last_admin_alert_at,
        "admin_alerts_skipped_rate_limit": state.admin_alerts_skipped_rate_limit,
        "last_web_alert_at": state.last_web_alert_at,
        "web_alerts_skipped_rate_limit": state.web_alerts_skipped_rate_limit,
        "last_redis_alert_at": state.last_redis_alert_at,
        "redis_alerts_skipped_rate_limit": state.redis_alerts_skipped_rate_limit,
        "last_rollback_alert_at": state.last_rollback_alert_at,
        "rollback_alerts_skipped_rate_limit": state.rollback_alerts_skipped_rate_limit,
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
    # Обновление runtime-конфига (если есть)
    refresh_config: Optional[Callable[[], Awaitable[None]]] = None,
    base_interval_s: float = 30.0,
    max_backoff_s: float = 300.0,
    min_notify_interval_s: float = 60.0,
    max_items_in_message: int = 10,
    store: Optional[StateStore] = None,
    store_key: str = "bot:polling_state",
    service_icon_store: Optional[ServiceIconStore] = None,
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

                if refresh_config is not None:
                    await refresh_config()

                # --- 1) Эскалации (не зависят от изменения снэпшота) ---
                if notify_escalation is not None and get_escalations is not None:
                    escalations = get_escalations(res.items)
                    if escalations:
                        await notify_escalation(escalations, "ESCALATION")

                # --- 2) Основной список (как раньше — только при изменении) ---
                snapshot_hash, ids = make_ids_snapshot_hash(res.items)

                state.last_calculated_count = len(ids)
                state.last_calculated_at = time.time()

                changed = (state.last_sent_snapshot is None) or (snapshot_hash != state.last_sent_snapshot)

                if changed:
                    normalized = normalize_tasks_for_message(res.items)
                    service_icons: dict[int, str] = {}
                    if service_icon_store is not None:
                        try:
                            icons = await service_icon_store.list_enabled()
                            service_icons = {i.service_id: i.icon for i in icons if i.icon}
                        except Exception:
                            service_icons = {}
                    text = _fmt_state_message(
                        normalized_items=normalized,
                        max_items_in_message=max_items_in_message,
                        service_icons=service_icons,
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
