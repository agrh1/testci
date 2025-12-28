"""
Telegram-бот (aiogram v3).

Шаг 12:
- Добавляем ENVIRONMENT=staging|prod|local (или unknown) и показываем его в /status.

Требования тестов:
- ping_reply_text() -> "pong ✅"
- HEALTH_URL строится из WEB_BASE_URL и всегда оканчивается на "/health"
  независимо от наличия "/" в конце WEB_BASE_URL.

Архитектурная договорённость:
- bot и web условно зависимые: бот должен жить даже если web недоступен.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message


def _build_health_url(web_base_url: str) -> str:
    """
    Собирает HEALTH_URL так, чтобы в итоге было строго "<base>/health".

    Примеры:
    - "http://web:8000"  -> "http://web:8000/health"
    - "http://web:8000/" -> "http://web:8000/health"
    """
    base = (web_base_url or "").strip()
    if not base:
        # Если не задано — оставляем относительный путь.
        # Это удобно локально и не ломает импорт модуля.
        return "/health"
    return base.rstrip("/") + "/health"


# WEB_BASE_URL может быть задан в .env (например, http://web:8000).
WEB_BASE_URL = os.getenv("WEB_BASE_URL", "")
# Тесты ожидают, что HEALTH_URL — атрибут модуля.
HEALTH_URL = _build_health_url(WEB_BASE_URL)


# -----------------------------
# Pure-функции для unit-тестов
# -----------------------------

def ping_reply_text() -> str:
    """Фиксированный ответ команды /ping (контракт тестов проекта)."""
    return "pong ✅"


def start_reply_text() -> str:
    return (
        "Привет! Я бот сервиса.\n"
        "Команды:\n"
        "/ping — проверка связи\n"
        "/status — окружение и версия\n"
    )


def unknown_reply_text() -> str:
    return "Не понял команду. Используй /start."


@dataclass(frozen=True)
class AppInfo:
    environment: str
    git_sha: str


def get_app_info() -> AppInfo:
    return AppInfo(
        environment=os.getenv("ENVIRONMENT", "unknown"),
        git_sha=os.getenv("GIT_SHA", "unknown"),
    )


def status_reply_text() -> str:
    info = get_app_info()
    return (
        "Статус: ok\n"
        f"ENVIRONMENT: {info.environment}\n"
        f"GIT_SHA: {info.git_sha}\n"
    )


def must_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value


# -----------------------------
# Aiogram wiring
# -----------------------------

dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(start_reply_text())


@dp.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    await message.answer(ping_reply_text())


@dp.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await message.answer(status_reply_text())


@dp.message(F.text)
async def fallback(message: Message) -> None:
    await message.answer(unknown_reply_text())


async def main() -> None:
    bot = Bot(token=must_env("TELEGRAM_BOT_TOKEN"))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
