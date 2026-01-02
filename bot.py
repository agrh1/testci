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
from bot.utils.polling import PollingState, polling_loop
from bot.utils.sd_web_client import SdWebClient
from bot.utils.web_client import WebClient
from bot.utils.web_filters import WebReadyFilter
from bot.utils.web_guard import WebGuard


def _get_env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"ENV {name} is required but not set")
    return value if value is not None else ""


def _format_check_line(title: str, ok: bool, status: Optional[int], duration_ms: int, request_id: str, error: Optional[str]) -> str:
    icon = "✅" if ok else "❌"
    status_s = str(status) if status is not None else "—"
    err = f", err={error}" if error else ""
    return f"{icon} {title}: status={status_s}, {duration_ms}ms, request_id={request_id}{err}"


def _fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


async def on_error(event: ErrorEvent) -> None:
    # Глобальный обработчик ошибок апдейтов: логируем всё, чтобы не было "тихих" падений.
    logger = logging.getLogger("bot.errors")
    logger.exception("Unhandled exception in update handling: %s", event.exception)


async def cmd_start(message: Message) -> None:
    await message.answer("Привет! Команды: /ping /status /needs_web")


async def cmd_ping(message: Message) -> None:
    await message.answer(ping_reply_text())


async def cmd_status(message: Message, web_client: WebClient, polling_state: PollingState) -> None:
    env = _get_env("ENVIRONMENT", "unknown")
    git_sha = _get_env("GIT_SHA", "unknown")
    web_base_url = _get_env("WEB_BASE_URL", "http://web:8000")
    poll_url = _get_env("POLL_URL", f"{web_base_url.rstrip('/')}/ready")

    health, ready = await web_client.check_health_ready(force=True)

    lines = [
        f"ENVIRONMENT: {env}",
        f"GIT_SHA: {git_sha}",
        f"WEB_BASE_URL: {web_base_url}",
        f"POLL_URL: {poll_url}",
        "",
        _format_check_line("web.health", health.ok, health.status, health.duration_ms, health.request_id, health.error),
        _format_check_line("web.ready", ready.ok, ready.status, ready.duration_ms, ready.request_id, ready.error),
        "",
        "POLLING:",
        f"- runs: {polling_state.runs}",
        f"- failures: {polling_state.failures} (consecutive={polling_state.consecutive_failures})",
        f"- last_run: {_fmt_ts(polling_state.last_run_ts)}",
        f"- last_success: {_fmt_ts(polling_state.last_success_ts)}",
        f"- last_http_status: {polling_state.last_http_status if polling_state.last_http_status is not None else '—'}",
        f"- last_request_id: {polling_state.last_request_id or '—'}",
        f"- last_error: {polling_state.last_error or '—'}",
        f"- last_duration_ms: {polling_state.last_duration_ms if polling_state.last_duration_ms is not None else '—'}",
        f"- current_interval_s: {polling_state.current_interval_s if polling_state.current_interval_s is not None else '—'}",
        f"- last_cursor: {polling_state.last_cursor or '—'}",
    ]
    await message.answer("\n".join(lines))


async def cmd_needs_web(message: Message) -> None:
    # guard выполняется фильтром WebReadyFilter
    await message.answer("web готов ✅ (дальше будет реальная бизнес-логика)")



async def cmd_sd_open(message: Message, sd_web_client: SdWebClient) -> None:
    res = await sd_web_client.get_open(limit=20)

    if not res.ok:
        # request_id полезен для поиска в логах web
        rid = f"\nrequest_id={res.request_id}" if res.request_id else ""
        await message.answer(f"❌ Не удалось получить заявки из ServiceDesk.{rid}\nПричина: {res.error}")
        return

    if not res.items:
        await message.answer(f"📌 Открытые заявки (StatusId={res.status_id}): пусто ✅")
        return

    lines = [
        f"📌 Открытые заявки (StatusId={res.status_id})",
        f"Показано: {res.count_returned}",
        "",
    ]

    # Показываем первые 20 (web и так ограничивает limit, но страхуемся)
    for t in res.items[:20]:
        # IntraService поля приходят как Id/Name (ровно как просили fields)
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

    # WebClient/WebGuard
    web_client = WebClient(
        base_url=web_base_url,
        timeout_s=float(os.getenv("WEB_TIMEOUT_S", "1.5")),
        cache_ttl_s=float(os.getenv("WEB_CACHE_TTL_S", "3.0")),
    )
    web_guard = WebGuard(web_client)

    # Polling settings
    polling_state = PollingState()
    stop_event = asyncio.Event()

    poll_url = _get_env("POLL_URL", f"{web_base_url}/ready")
    poll_interval_s = float(os.getenv("POLL_INTERVAL_S", "30"))
    poll_timeout_s = float(os.getenv("POLL_TIMEOUT_S", "2"))
    poll_max_backoff_s = float(os.getenv("POLL_MAX_BACKOFF_S", "300"))
    poll_max_retries = int(os.getenv("POLL_MAX_RETRIES", "2"))
    poll_retry_base_delay_s = float(os.getenv("POLL_RETRY_BASE_DELAY_S", "0.5"))

    bot = Bot(token=token)
    dp = Dispatcher()

    # DI
    dp.workflow_data["web_client"] = web_client
    dp.workflow_data["web_guard"] = web_guard
    dp.workflow_data["polling_state"] = polling_state
    sd_web_client = SdWebClient(base_url=web_base_url, timeout_s=float(os.getenv("SD_WEB_TIMEOUT_S", "3")))
    dp.workflow_data["sd_web_client"] = sd_web_client
    # Глобальный error handler
    dp.errors.register(on_error)

    # Команды
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_ping, Command("ping"))
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_needs_web, Command("needs_web"), WebReadyFilter("/needs_web"))
    dp.message.register(cmd_sd_open, Command("sd_open"))


    # Запуск polling
    polling_task = asyncio.create_task(
        polling_loop(
            state=polling_state,
            stop_event=stop_event,
            url=poll_url,
            base_interval_s=poll_interval_s,
            timeout_s=poll_timeout_s,
            max_backoff_s=poll_max_backoff_s,
            max_retries=poll_max_retries,
            retry_base_delay_s=poll_retry_base_delay_s,
        ),
        name="polling_loop",
    )

    logger.info(
        "Bot started. WEB_BASE_URL=%s POLL_URL=%s POLL_INTERVAL_S=%s POLL_MAX_RETRIES=%s",
        web_base_url,
        poll_url,
        poll_interval_s,
        poll_max_retries,
    )

    try:
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        polling_task.cancel()
        with contextlib.suppress(Exception):
            await polling_task


if __name__ == "__main__":
    asyncio.run(main())
