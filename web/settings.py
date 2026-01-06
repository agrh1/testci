"""
Настройки web-сервиса.

Задачи модуля:
- единая точка чтения env;
- дефолты и валидация;
- общие списки для readiness.
"""

from __future__ import annotations

import os

ALLOWED_ENVIRONMENTS = {"staging", "prod", "local"}

# Обязательные переменные для readiness.
REQUIRED_ENV_VARS = [
    "SERVICEDESK_BASE_URL",
    "SERVICEDESK_LOGIN",
    "SERVICEDESK_PASSWORD",
    "TELEGRAM_BOT_TOKEN",
]

# Необязательные, но полезные для диагностики.
OPTIONAL_ENV_VARS = [
    "SERVICEDESK_API_TOKEN",
    "SERVICEDESK_TIMEOUT_S",
]


def get_env(name: str, default: str | None = None) -> str:
    """
    Читает переменную окружения как строку.
    """
    value = os.getenv(name, default)
    return value if value is not None else ""


def get_environment() -> str:
    return get_env("ENVIRONMENT", "unknown")


def get_git_sha() -> str:
    return get_env("GIT_SHA", "unknown")


def is_strict_readiness() -> bool:
    return get_env("STRICT_READINESS", "0").strip() == "1"


def get_servicedesk_timeout_s() -> float:
    raw = get_env("SERVICEDESK_TIMEOUT_S", "10").strip()
    try:
        return float(raw)
    except Exception:
        return 10.0


def build_flask_config() -> dict[str, object]:
    """
    Собирает словарь для app.config.
    """
    return {
        "ENVIRONMENT": get_environment(),
        "GIT_SHA": get_git_sha(),
        "SERVICEDESK_BASE_URL": get_env("SERVICEDESK_BASE_URL", "").strip(),
        "SERVICEDESK_LOGIN": get_env("SERVICEDESK_LOGIN", "").strip(),
        "SERVICEDESK_PASSWORD": get_env("SERVICEDESK_PASSWORD", "").strip(),
        "SERVICEDESK_TIMEOUT_S": get_servicedesk_timeout_s(),
        "CONFIG_TOKEN": get_env("CONFIG_TOKEN", "").strip(),
        "CONFIG_ADMIN_TOKEN": get_env("CONFIG_ADMIN_TOKEN", "").strip(),
    }
