from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import ErrorEvent, Message

from bot import ping_reply_text
from bot.utils.polling import PollingState, polling_open_queue_loop
from bot.utils.sd_web_client import SdWebClient
from bot.utils.state_store import MemoryStateStore, RedisStateStore, ResilientStateStore, StateStore
from bot.utils.web_client import WebClient
from bot.utils.web_filters import WebReadyFilter
from bot.utils.web_guard import WebGuard


def _get_env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"ENV {name} is required but not set")
    return value if value is not None else ""


def _fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _format_check_line(
    title: str,
    ok: bool,
    status: Optional[int],
    duration_ms: int,
    request_id: str,
    error: Optional[str],
) -> str:
    icon = "✅" if ok else "❌"
    status_s = str(status) if status is not None else "—"
    err = f", err={error}" if error else ""
    return f"{icon} {title}: status={status_s}, {duration_ms}ms, request_id={request_id}{err}"


async def on_error(event: ErrorEvent) -> None:
    logger = logging.getLogger("bot.errors")
    logger.exception("Unhandled exception in update handling: %s", event.exception)


async def cmd_start(message: Message) -> None:
    await message.answer("Привет! Команды: /ping /status /needs_web /sd_open")


async def cmd_ping(message: Message) -> None:
    await message.answer(ping_reply_text())


async def cmd_status(
    message: Message,
    web_client: WebClient,
    polling_state: PollingState,
    state_store: Optional[StateStore],
) -> None:
    env = _get_env("ENVIRONMENT", "unknown")
    git_sha = _get_env("GIT_SHA", "unknown")
    web_base_url = _get_env("WEB_BASE_URL", "http://web:8000")
    alert_chat_id = _get_env("ALERT_CHAT_ID", "")

    # Шаг 24: перед выводом статуса активно проверяем Redis (если store умеет ping),
    # чтобы backend отображал реальность "прямо сейчас", а не последнее состояние.
    if state_store is not None:
        ping_fn = getattr(state_store, "ping", None)
        if callable(ping_fn):
            with contextlib.suppress(Exception):
                ping_fn()

    store_backend = state_store.backend() if state_store is not None else "disabled"
    store_last_error = getattr(state_store, "last_error", None) if state_store is not None else None
    store_last_ok_ts = getattr(state_store, "last_ok_ts", None) if state_store is not None else None

    health, ready = await web_client.check_health_ready(force=True)

    lines = [
        f"ENVIRONMENT: {env}",
        f"GIT_SHA: {git_sha}",
        f"WEB_BASE_URL: {web_base_url}",
        f"ALERT_CHAT_ID: {alert_chat_id or '—'}",
        "",
        "STATE STORE:",
        f"- enabled: {'yes' if state_store is not None else 'no'}",
        f"- backend: {store_backend}",
        f"- last_redis_ok: {_fmt_ts(store_last_ok_ts) if store_last_ok_ts else '—'}",
        f"- last_redis_error: {store_last_error or '—'}",
        "",
        _format_check_line("web.health", health.ok, health.status, health.duration_ms, health.request_id, health.error),
        _format_check_line("web.ready", ready.ok, ready.status, ready.duration_ms, ready.request_id, ready.error),
        "",
        "SD QUEUE POLLING:",
        f"- runs: {polling_state.runs}",
        f"- failures: {polling_state.failures} (consecutive={polling_state.consecutive_failures})",
        f"- last_run: {_fmt_ts(polling_state.last_run_ts)}",
        f"- last_success: {_fmt_ts(polling_state.last_success_ts)}",
        f"- last_error: {polling_state.last_error or '—'}",
        f"- last_duration_ms: {polling_state.last_duration_ms if polling_state.last_duration_ms is not None else '—'}",
        "",
        "SD QUEUE SNAPSHOT:",
        f"- last_calculated_at: {_fmt_ts(polling_state.last_calculated_at)}",
        f"- last_calculated_count: {polling_state.last_calculated_count if polling_state.last_calculated_count is not None else '—'}",
        f"- last_sent_at: {_fmt_ts(polling_state.last_sent_at)}",
        f"- last_sent_count: {polling_state.last_sent_count if polling_state.last_sent_count is not None else '—'}",
        f"- last_sent_snapshot: {polling_state.last_sent_snapshot or '—'}",
        f"- last_sent_ids: {polling_state.last_sent_ids if polling_state.last_sent_ids is not None else '—'}",
        "",
        "NOTIFY RATE-LIMIT:",
        f"- last_notify_attempt_at: {_fmt_ts(polling_state.last_notify_attempt_at)}",
        f"- notify_skipped_rate_limit: {polling_state.notify_skipped_rate_limit}",
    ]
    await message.answer("\n".join(lines))


async def cmd_needs_web(message: Message) -> None:
    await message.answer("web готов ✅ (дальше будет реальная бизнес-логика)")


async def cmd_sd_open(message: Message, sd_web_client: SdWebClient) -> None:
    res = await sd_web_client.get_open(limit=20)
    if not res.ok:
        rid = f"\nrequest_id={res.request_id}" if res.request_id else ""
        await message.answer(f"❌ Не удалось получить заявки из ServiceDesk.{rid}\nПричина: {res.error}")
        return

    if not res.items:
        await message.answer("📌 Открытых заявок нет ✅")
        return

    lines = [f"📌 Открытые заявки: {res.count_returned}", ""]
    for t in res.items[:20]:
        lines.append(f"- #{t.get('Id')}: {t.get('Name')}")
    await message.answer("\n".join(lines))


async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("bot")

    token = _get_env("TELEGRAM_BOT_TOKEN", required=True)
    web_base_url = _get_env("WEB_BASE_URL", "http://web:8000").rstrip("/")

    web_client = WebClient(
        base_url=web_base_url,
        timeout_s=float(os.getenv("WEB_TIMEOUT_S", "1.5")),
        cache_ttl_s=float(os.getenv("WEB_CACHE_TTL_S", "3.0")),
    )
    web_guard = WebGuard(web_client)

    sd_web_client = SdWebClient(
        base_url=web_base_url,
        timeout_s=float(os.getenv("SD_WEB_TIMEOUT_S", "3")),
    )

    # state store (шаг 24)
    redis_url = os.getenv("REDIS_URL", "").strip()
    state_store: Optional[StateStore] = None
    if redis_url:
        socket_timeout_s = float(os.getenv("REDIS_SOCKET_TIMEOUT_S", "1.0"))
        socket_connect_timeout_s = float(os.getenv("REDIS_CONNECT_TIMEOUT_S", "1.0"))
        primary = RedisStateStore(
            redis_url,
            prefix="testci",
            socket_timeout_s=socket_timeout_s,
            socket_connect_timeout_s=socket_connect_timeout_s,
        )
        fallback = MemoryStateStore(prefix="testci")
        state_store = ResilientStateStore(primary, fallback)

        # Пинг на старте, чтобы backend был корректный сразу
        with contextlib.suppress(Exception):
            getattr(state_store, "ping", lambda: None)()

    polling_state = PollingState()
    stop_event = asyncio.Event()

    poll_interval_s = float(os.getenv("POLL_INTERVAL_S", "30"))
    poll_max_backoff_s = float(os.getenv("POLL_MAX_BACKOFF_S", "300"))

    min_notify_interval_s = float(os.getenv("MIN_NOTIFY_INTERVAL_S", "60"))
    max_items_in_message = int(os.getenv("MAX_ITEMS_IN_MESSAGE", "10"))

    alert_chat_id_raw = os.getenv("ALERT_CHAT_ID", "").strip()
    alert_chat_id = int(alert_chat_id_raw) if alert_chat_id_raw else None

    bot = Bot(token=token)
    dp = Dispatcher()

    dp.workflow_data["web_client"] = web_client
    dp.workflow_data["web_guard"] = web_guard
    dp.workflow_data["sd_web_client"] = sd_web_client
    dp.workflow_data["polling_state"] = polling_state
    dp.workflow_data["state_store"] = state_store

    dp.errors.register(on_error)

    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_ping, Command("ping"))
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_sd_open, Command("sd_open"))
    dp.message.register(cmd_needs_web, Command("needs_web"), WebReadyFilter("/needs_web"))

    async def notify(text: str) -> None:
        if alert_chat_id is None:
            logging.getLogger("bot.polling").info(
                "ALERT_CHAT_ID not set, skip notify: %s",
                text.replace("\n", " | "),
            )
            return
        await bot.send_message(chat_id=alert_chat_id, text=text)

    polling_task = asyncio.create_task(
        polling_open_queue_loop(
            state=polling_state,
            stop_event=stop_event,
            sd_web_client=sd_web_client,
            notify=notify,
            base_interval_s=poll_interval_s,
            max_backoff_s=poll_max_backoff_s,
            min_notify_interval_s=min_notify_interval_s,
            max_items_in_message=max_items_in_message,
            store=state_store,
            store_key="bot:open_queue",
        ),
        name="polling_open_queue",
    )

    logger.info(
        "Bot started. WEB_BASE_URL=%s POLL_INTERVAL_S=%s MIN_NOTIFY_INTERVAL_S=%s MAX_ITEMS_IN_MESSAGE=%s ALERT_CHAT_ID=%s",
        web_base_url,
        poll_interval_s,
        min_notify_interval_s,
        max_items_in_message,
        alert_chat_id_raw or "—",
    )

    try:
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        polling_task.cancel()
        with contextlib.suppress(Exception):
            await polling_task


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
