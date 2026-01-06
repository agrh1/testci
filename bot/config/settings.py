"""
Загрузка настроек приложения из переменных окружения.

Модуль нужен, чтобы:
- собрать все env в одном месте;
- дать единые дефолты и валидацию;
- упростить чтение в main и хендлерах.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def get_env(name: str, default: str | None = None, required: bool = False) -> str:
    """
    Читает переменную окружения как строку.

    Если required=True и переменная пустая — выбрасываем RuntimeError.
    """
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"ENV {name} is required but not set")
    return value if value is not None else ""


def get_env_float(name: str, default: str) -> float:
    """
    Читает float из env, либо использует default.
    """
    return float(os.getenv(name, default))


def get_env_int(name: str, default: str) -> int:
    """
    Читает int из env, либо использует default.
    """
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class BotSettings:
    """
    Все настройки бота, собранные в один объект.
    """
    token: str
    web_base_url: str
    log_level: str
    web_timeout_s: float
    web_cache_ttl_s: float
    sd_web_timeout_s: float
    config_url: str
    config_token: str
    config_ttl_s: float
    config_timeout_s: float
    redis_url: str
    redis_socket_timeout_s: float
    redis_connect_timeout_s: float
    poll_interval_s: float
    poll_max_backoff_s: float
    min_notify_interval_s: float
    max_items_in_message: int

    @classmethod
    def from_env(cls) -> "BotSettings":
        """
        Считывает настройки из окружения с дефолтами.
        """
        log_level = get_env("LOG_LEVEL", "INFO")

        token = get_env("TELEGRAM_BOT_TOKEN", required=True)

        web_base_url = get_env("WEB_BASE_URL", "http://web:8000").rstrip("/")

        web_timeout_s = get_env_float("WEB_TIMEOUT_S", "1.5")
        web_cache_ttl_s = get_env_float("WEB_CACHE_TTL_S", "3.0")
        sd_web_timeout_s = get_env_float("SD_WEB_TIMEOUT_S", "3")

        config_url_default = f"{web_base_url}/config"
        config_url = get_env("CONFIG_URL", config_url_default).strip() or config_url_default
        config_token = get_env("CONFIG_TOKEN", "").strip()
        config_ttl_s = get_env_float("CONFIG_TTL_S", "60")
        config_timeout_s = get_env_float("CONFIG_TIMEOUT_S", "2.5")

        redis_url = get_env("REDIS_URL", "").strip()
        redis_socket_timeout_s = get_env_float("REDIS_SOCKET_TIMEOUT_S", "1.0")
        redis_connect_timeout_s = get_env_float("REDIS_CONNECT_TIMEOUT_S", "1.0")

        poll_interval_s = get_env_float("POLL_INTERVAL_S", "30")
        poll_max_backoff_s = get_env_float("POLL_MAX_BACKOFF_S", "300")
        min_notify_interval_s = get_env_float("MIN_NOTIFY_INTERVAL_S", "60")
        max_items_in_message = get_env_int("MAX_ITEMS_IN_MESSAGE", "10")

        return cls(
            token=token,
            web_base_url=web_base_url,
            log_level=log_level,
            web_timeout_s=web_timeout_s,
            web_cache_ttl_s=web_cache_ttl_s,
            sd_web_timeout_s=sd_web_timeout_s,
            config_url=config_url,
            config_token=config_token,
            config_ttl_s=config_ttl_s,
            config_timeout_s=config_timeout_s,
            redis_url=redis_url,
            redis_socket_timeout_s=redis_socket_timeout_s,
            redis_connect_timeout_s=redis_connect_timeout_s,
            poll_interval_s=poll_interval_s,
            poll_max_backoff_s=poll_max_backoff_s,
            min_notify_interval_s=min_notify_interval_s,
            max_items_in_message=max_items_in_message,
        )
