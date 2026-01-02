from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.request
import uuid
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message


def get_environment() -> str:
    return os.getenv("ENVIRONMENT", "unknown")


def get_git_sha() -> str:
    return os.getenv("GIT_SHA", "unknown")


# -----------------------------
# Logging
# -----------------------------

class ContextAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        extra = kwargs.get("extra", {})
        extra.setdefault("environment", get_environment())
        extra.setdefault("git_sha", get_git_sha())
        kwargs["extra"] = extra
        return msg, kwargs


def setup_logging() -> ContextAdapter:
    logger = logging.getLogger("testci.bot")
    if logger.handlers:
        return ContextAdapter(logger, {})

    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        fmt=(
            "ts=%(asctime)s level=%(levelname)s service=bot "
            "env=%(environment)s sha=%(git_sha)s "
            "msg=%(message)s"
        )
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return ContextAdapter(logger, {})


log = setup_logging()


# -----------------------------
# URL building
# -----------------------------

def _build_url(web_base_url: str, path: str) -> str:
    base = (web_base_url or "").strip()
    if not base:
        return path
    return base.rstrip("/") + path


WEB_BASE_URL = os.getenv("WEB_BASE_URL", "")
HEALTH_URL = _build_url(WEB_BASE_URL, "/health")
READY_URL = _build_url(WEB_BASE_URL, "/ready")


# -----------------------------
# Reply texts (контракты тестов)
# -----------------------------

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
    return AppInfo(environment=get_environment(), git_sha=get_git_sha())


@dataclass(frozen=True)
class HttpCheck:
    name: str
    url: str
    ok: bool
    http_status: int | None = None
    error: str | None = None
    duration_ms: int | None = None
    request_id: str | None = None  # <-- для корреляции


def _sync_fetch_json(url: str, timeout_seconds: float, request_id: str | None) -> tuple[int, object]:
    headers = {"User-Agent": "testci-bot/1.0"}
    if request_id:
        headers["X-Request-ID"] = request_id

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        status = int(resp.status)
        body = resp.read().decode("utf-8")
        return status, json.loads(body)


async def _check_endpoint(
    name: str,
    url: str,
    request_id: str | None,
    timeout_seconds: float = 1.5,
) -> HttpCheck:
    if url.startswith("/"):
        return HttpCheck(name=name, url=url, ok=False, error="WEB_BASE_URL не задан", request_id=request_id)

    start = time.perf_counter()
    try:
        http_status, data = await asyncio.to_thread(_sync_fetch_json, url, timeout_seconds, request_id)
        duration_ms = int((time.perf_counter() - start) * 1000)

        if name == "web.health":
            ok = http_status == 200 and isinstance(data, dict) and data.get("status") == "ok"
        else:
            ok = http_status == 200 and isinstance(data, dict) and data.get("ready") is True

        if not ok:
            return HttpCheck(
                name=name,
                url=url,
                ok=False,
                http_status=http_status,
                error="Ответ не подтверждает OK",
                duration_ms=duration_ms,
                request_id=request_id,
            )
        return HttpCheck(
            name=name,
            url=url,
            ok=True,
            http_status=http_status,
            duration_ms=duration_ms,
            request_id=request_id,
        )
    except Exception as e:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return HttpCheck(
            name=name,
            url=url,
            ok=False,
            error=str(e),
            duration_ms=duration_ms,
            request_id=request_id,
        )


def format_status_text(app_info: AppInfo, checks: list[HttpCheck], request_id: str) -> str:
    lines = [
        "Статус: ok",
        f"ENVIRONMENT: {app_info.environment}",
        f"GIT_SHA: {app_info.git_sha}",
        "",
        f"request_id: {request_id}",
        "",
        "WEB checks:",
    ]
    for c in checks:
        lines.append(f"- {c.name}: {'ok' if c.ok else 'fail'}")
        lines.append(f"  url: {c.url}")
        if c.http_status is not None:
            lines.append(f"  http_status: {c.http_status}")
        if c.duration_ms is not None:
            lines.append(f"  duration_ms: {c.duration_ms}")
        if c.error:
            lines.append(f"  error: {c.error}")
    return "\n".join(lines)

def get_telegram_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задана переменная окружения TELEGRAM_BOT_TOKEN")
    return token

dp = Dispatcher()

def _msg_ctx(message: Message) -> str:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    return f"user_id={user_id} chat_id={chat_id}"

@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    log.info("command=/start %s", _msg_ctx(message))
    await message.answer(start_reply_text())

@dp.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    log.info("command=/ping %s", _msg_ctx(message))
    await message.answer(ping_reply_text())

@dp.message(Command("status"))
async def cmd_status(message: Message) -> None:
    request_id = uuid.uuid4().hex  # <-- единый id на команду
    log.info("command=/status request_id=%s %s", request_id, _msg_ctx(message))
    info = get_app_info()
    health_task = _check_endpoint("web.health", HEALTH_URL, request_id=request_id)
    ready_task = _check_endpoint("web.ready", READY_URL, request_id=request_id)
    checks = await asyncio.gather(health_task, ready_task)

    log.info(
        "web_checks request_id=%s health_ok=%s ready_ok=%s health_ms=%s ready_ms=%s",
        request_id,
        checks[0].ok,
        checks[1].ok,
        checks[0].duration_ms,
        checks[1].duration_ms,
    )

    await message.answer(format_status_text(info, checks, request_id=request_id))


@dp.message(F.text)
async def fallback(message: Message) -> None:
    log.info("message=text %s", _msg_ctx(message))
    await message.answer(unknown_reply_text())


async def main() -> None:
    bot = Bot(token=get_telegram_token())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
