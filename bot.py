"""
bot.py — Telegram-бот на aiogram v3.

Функциональность:
- Лог при старте: "бот запущен и работает"
- /start — приветствие
- /ping  — быстрый признак "жив"
- /status — показывает текущую версию (GIT_SHA) и настройки health

Пояснение про версию:
- Переменная окружения GIT_SHA задаётся на этапе сборки Docker-образа (ARG/ENV).
"""

import asyncio
import logging
import os
import signal
from typing import Optional

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("bot")


def env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Безопасное чтение переменных окружения (удобно для Docker)."""
    value = os.getenv(name, default)
    if value is not None:
        value = value.strip()
    return value


def get_git_sha() -> str:
    """
    Текущая версия приложения (commit SHA).
    В контейнер попадает на этапе сборки Docker-образа через ARG/ENV.
    """
    return env("GIT_SHA", "unknown") or "unknown"


TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
WEB_BASE_URL = env("WEB_BASE_URL", "http://web:8000")
HEALTH_INTERVAL_SEC = int(env("HEALTH_INTERVAL_SEC", "5"))

HEALTH_URL = f"{WEB_BASE_URL.rstrip('/')}/health"


def ping_reply_text() -> str:
    """Отдельная функция — удобно тестировать без Telegram."""
    return "pong ✅"


def status_reply_text() -> str:
    """Текст статуса бота: версия и базовые параметры."""
    return (
        "Статус ✅\n"
        f"Версия (GIT_SHA): {get_git_sha()}\n"
        f"HEALTH_URL: {HEALTH_URL}\n"
        f"HEALTH_INTERVAL_SEC: {HEALTH_INTERVAL_SEC}"
    )


async def healthcheck_loop(session: aiohttp.ClientSession) -> None:
    """Фоновая проверка /health у web-сервиса. На Telegram-часть не влияет."""
    logger.info("Starting healthcheck loop for %s, interval=%ss", HEALTH_URL, HEALTH_INTERVAL_SEC)

    while True:
        try:
            async with session.get(HEALTH_URL, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                text = await resp.text()
                logger.info("[health] %s %s", resp.status, text)
        except Exception as e:
            logger.warning("[health] error: %s", e)

        await asyncio.sleep(HEALTH_INTERVAL_SEC)


async def cmd_start(message: Message) -> None:
    await message.answer("Я запущен и работаю ✅\nПроверь: /ping\nВерсия: /status")


async def cmd_ping(message: Message) -> None:
    await message.answer(ping_reply_text())


async def cmd_status(message: Message) -> None:
    await message.answer(status_reply_text())


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error(
            "TELEGRAM_BOT_TOKEN не задан. Добавь токен в env (например, .envs/.env.local) и перезапусти контейнер."
        )
        raise SystemExit(1)

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    # Команды
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_ping, Command("ping"))
    dp.message.register(cmd_status, Command("status"))

    # Удобный хэндлер для текста "ping"
    @dp.message(F.text.lower() == "ping")
    async def ping_text(message: Message) -> None:
        await message.answer("pong ✅ (text)")

    # Фолбэк: чтобы не было "Update is not handled"
    @dp.message()
    async def fallback(message: Message) -> None:
        await message.answer("Я на связи ✅ Команды: /start, /ping, /status")

    # Корректное завершение по SIGTERM/SIGINT в Docker
    stop_event = asyncio.Event()

    def _graceful_shutdown(*_: object) -> None:
        logger.info("Shutdown signal received, stopping...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _graceful_shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _graceful_shutdown())

    logger.info("Бот запущен и работает ✅ (polling), version=%s", get_git_sha())

    async with aiohttp.ClientSession() as session:
        health_task = asyncio.create_task(healthcheck_loop(session))
        polling_task = asyncio.create_task(dp.start_polling(bot))

        await stop_event.wait()

        polling_task.cancel()
        health_task.cancel()

        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
