"""
Telegram bot (aiogram v3).

Шаг 17: управляемая деградация web-зависимых команд.
- /status всегда работает и показывает состояние web (health/ready)
- /needs_web — пример web-зависимой команды (блокируется guard'ом)
- /ping — локальная команда, всегда работает

ВАЖНО:
aiogram v3 умеет DI: если в dp.workflow_data положить объекты,
то их можно принимать как параметры хендлера.
Это надёжнее, чем пытаться доставать dispatcher из Message.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from bot import ping_reply_text  # legacy для тестов
from bot.utils.web_client import WebClient
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


async def cmd_start(message: Message) -> None:
    await message.answer("Привет! Команды: /ping /status /needs_web")


async def cmd_ping(message: Message) -> None:
    # Локальная команда: всегда работает, не зависит от web
    await message.answer(ping_reply_text())


async def cmd_status(message: Message, web_client: WebClient) -> None:
    """
    Локальная команда: не должна блокироваться guard'ом.
    Показывает health/ready web-сервиса.
    """
    env = _get_env("ENVIRONMENT", "unknown")
    git_sha = _get_env("GIT_SHA", "unknown")
    web_base_url = _get_env("WEB_BASE_URL", "http://web:8000")

    health, ready = await web_client.check_health_ready(force=True)

    lines = [
        f"ENVIRONMENT: {env}",
        f"GIT_SHA: {git_sha}",
        f"WEB_BASE_URL: {web_base_url}",
        "",
        _format_check_line("web.health", health.ok, health.status, health.duration_ms, health.request_id, health.error),
        _format_check_line("web.ready", ready.ok, ready.status, ready.duration_ms, ready.request_id, ready.error),
    ]
    await message.answer("\n".join(lines))


async def cmd_needs_web(message: Message, web_guard: WebGuard) -> None:
    """
    Пример web-зависимой команды. На шаге 17 она ничего не делает, кроме демонстрации guard.
    """
    if not await web_guard.require_web(message, friendly_name="/needs_web"):
        return

    await message.answer("web готов ✅ (здесь дальше будет реальная бизнес-логика)")


async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("bot")

    token = _get_env("TELEGRAM_BOT_TOKEN", required=True)
    web_base_url = _get_env("WEB_BASE_URL", "http://web:8000")

    # WebClient/WebGuard (шаг 17)
    web_client = WebClient(
        base_url=web_base_url,
        timeout_s=float(os.getenv("WEB_TIMEOUT_S", "1.5")),
        cache_ttl_s=float(os.getenv("WEB_CACHE_TTL_S", "3.0")),
    )
    web_guard = WebGuard(web_client)

    bot = Bot(token=token)
    dp = Dispatcher()

    # ✅ DI: складываем зависимости сюда
    dp.workflow_data["web_client"] = web_client
    dp.workflow_data["web_guard"] = web_guard

    # Роутинг команд
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_ping, Command("ping"))
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_needs_web, Command("needs_web"))

    logger.info("Bot started. WEB_BASE_URL=%s", web_base_url)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
