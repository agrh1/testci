"""
Telegram-бот (aiogram v3).

Шаг 12:
- /status показывает ENVIRONMENT и GIT_SHA.

Шаг 13 (с смыслом):
- /status дополнительно показывает доступность web по:
  - /health (liveness)
  - /ready  (readiness)

Важно:
- bot и web условно зависимые: бот живёт независимо от web.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message


def _build_url(web_base_url: str, path: str) -> str:
    base = (web_base_url or "").strip()
    if not base:
        return path  # относительный путь, если WEB_BASE_URL не задан
    return base.rstrip("/") + path


WEB_BASE_URL = os.getenv("WEB_BASE_URL", "")
HEALTH_URL = _build_url(WEB_BASE_URL, "/health")  # контракт сохранён
READY_URL = _build_url(WEB_BASE_URL, "/ready")


def ping_reply_text() -> str:
    return "pong ✅"


def start_reply_text() -> str:
    return (
        "Привет! Я бот сервиса.\n"
        "Команды:\n"
        "/ping — проверка связи\n"
        "/status — окружение, версия и состояние web (health/ready)\n"
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


@dataclass(frozen=True)
class HttpCheck:
    name: str
    url: str
    ok: bool
    http_status: int | None = None
    error: str | None = None


def _sync_fetch_json(url: str, timeout_seconds: float) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "testci-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        status = int(resp.status)
        body = resp.read().decode("utf-8")
        return status, json.loads(body)


async def _check_endpoint(name: str, url: str, timeout_seconds: float = 1.5) -> HttpCheck:
    # Если URL относительный — считаем, что конфиг не настроен (это не “падение web”).
    if url.startswith("/"):
        return HttpCheck(name=name, url=url, ok=False, error="WEB_BASE_URL не задан")

    try:
        http_status, data = await asyncio.to_thread(_sync_fetch_json, url, timeout_seconds)

        if name == "web.health":
            ok = http_status == 200 and isinstance(data, dict) and data.get("status") == "ok"
        else:
            # web.ready: принимаем 200 как ready, 503 как not_ready
            ok = http_status == 200 and isinstance(data, dict) and data.get("ready") is True

        return HttpCheck(name=name, url=url, ok=ok, http_status=http_status, error=None if ok else "Ответ не подтверждает OK")
    except Exception as e:
        return HttpCheck(name=name, url=url, ok=False, error=str(e))


def format_status_text(app_info: AppInfo, checks: list[HttpCheck]) -> str:
    lines = [
        "Статус: ok",
        f"ENVIRONMENT: {app_info.environment}",
        f"GIT_SHA: {app_info.git_sha}",
        "",
        "WEB checks:",
    ]
    for c in checks:
        lines.append(f"- {c.name}: {'ok' if c.ok else 'fail'}")
        lines.append(f"  url: {c.url}")
        if c.http_status is not None:
            lines.append(f"  http_status: {c.http_status}")
        if c.error:
            lines.append(f"  error: {c.error}")
    return "\n".join(lines)


def get_telegram_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задана переменная окружения TELEGRAM_BOT_TOKEN")
    return token


dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(start_reply_text())


@dp.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    await message.answer(ping_reply_text())


@dp.message(Command("status"))
async def cmd_status(message: Message) -> None:
    info = get_app_info()
    checks = [
        await _check_endpoint("web.health", HEALTH_URL),
        await _check_endpoint("web.ready", READY_URL),
    ]
    await message.answer(format_status_text(info, checks))


@dp.message(F.text)
async def fallback(message: Message) -> None:
    await message.answer(unknown_reply_text())


async def main() -> None:
    bot = Bot(token=get_telegram_token())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
