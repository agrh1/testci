# bot.py
# Асинхронный Telegram-бот на aiogram v3.
# 1) При старте пишет в лог "бот запущен и работает"
# 2) Команды /start и /ping для проверки работоспособности
# 3) Фоново проверяет health web-сервиса (WEB_BASE_URL + /health)
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
    """Читаем переменные окружения безопасно, чтобы проще отлаживать в Docker."""
    value = os.getenv(name, default)
    if value is not None:
        value = value.strip()
    return value


TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN")
WEB_BASE_URL = env("WEB_BASE_URL", "http://web:8000")
HEALTH_INTERVAL_SEC = int(env("HEALTH_INTERVAL_SEC", "5"))


async def healthcheck_loop(session: aiohttp.ClientSession) -> None:
    """Фоновая проверка /health у web-сервиса. Не влияет на Telegram-бот."""
    url = f"{WEB_BASE_URL.rstrip('/')}/health"
    logger.info("Starting healthcheck loop for %s, interval=%ss", url, HEALTH_INTERVAL_SEC)

    while True:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                text = await resp.text()
                logger.info("[health] %s %s", resp.status, text)
        except Exception as e:
            logger.warning("[health] error: %s", e)

        await asyncio.sleep(HEALTH_INTERVAL_SEC)


async def cmd_start(message: Message) -> None:
    await message.answer(
        "Я запущен и работаю ✅\n"
        "Проверь меня командой /ping"
    )

def build_health_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/health"

def ping_reply_text() -> str:
    """Отдельная функция — чтобы тестировать без Telegram."""
    return "pong ✅"

async def cmd_ping(message: Message) -> None:
    # Можно чуть больше инфы для диагностики
    await message.answer(ping_reply_text())


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error(
            "TELEGRAM_BOT_TOKEN не задан. Добавь токен в env (например, .env.local/.envs/.env.local) "
            "и перезапусти контейнер."
        )
        raise SystemExit(1)

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    # Роуты команд
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_ping, Command("ping"))

    # (Опционально) ответ на текст "ping" без слеша — удобно в отладке
    @dp.message(F.text.lower() == "ping")
    async def ping_text(message: Message) -> None:
        await message.answer("pong ✅ (text)")

    # Чтобы корректно закрываться по SIGTERM/SIGINT в Docker
    stop_event = asyncio.Event()

    def _graceful_shutdown(*_: object) -> None:
        logger.info("Shutdown signal received, stopping...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _graceful_shutdown)
        except NotImplementedError:
            # На Windows/некоторых окружениях add_signal_handler может быть недоступен
            signal.signal(sig, lambda *_: _graceful_shutdown())

    logger.info("Бот запущен и работает ✅ (polling)")


    # заглушка для необработанных сообщений
    @dp.message()
    async def fallback(message: Message) -> None:
        await message.answer("Я на связи ✅ Команды: /start, /ping")


    async with aiohttp.ClientSession() as session:
        health_task = asyncio.create_task(healthcheck_loop(session))

        # polling запускаем в отдельной задаче, чтобы можно было ждать stop_event
        polling_task = asyncio.create_task(dp.start_polling(bot))

        # ждём сигнал остановки
        await stop_event.wait()

        # останавливаемся
        polling_task.cancel()
        health_task.cancel()

        await bot.session.close()










if __name__ == "__main__":
    asyncio.run(main())
