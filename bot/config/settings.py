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


def parse_int_list(raw: str) -> list[int]:
    """
    Парсит список int из строки вида "1,2, 3".
    """
    out: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except Exception:
            continue
    return out


def normalize_database_url(url: str) -> str:
    """
    Нормализует DATABASE_URL для прямого подключения psycopg2.

    Web использует SQLAlchemy-формат: postgresql+psycopg2://...
    Для psycopg2 нужен postgresql://...
    """
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    return url


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
    servicedesk_base_url: str
    servicedesk_login: str
    servicedesk_password: str
    servicedesk_timeout_s: float
    config_url: str
    config_token: str
    config_ttl_s: float
    config_timeout_s: float
    config_admin_token: str
    database_url: str
    tg_admins: tuple[int, ...]
    tg_users: tuple[int, ...]
    redis_url: str
    redis_socket_timeout_s: float
    redis_connect_timeout_s: float
    poll_interval_s: float
    poll_max_backoff_s: float
    min_notify_interval_s: float
    max_items_in_message: int
    obs_check_interval_s: float
    obs_rollback_window_s: int
    obs_rollback_threshold: int
    admin_alert_min_interval_s: float
    obs_web_alert_min_interval_s: float
    obs_redis_alert_min_interval_s: float
    obs_rollback_alert_min_interval_s: float
    eventlog_base_url: str
    eventlog_poll_interval_s: int
    eventlog_keepalive_every: int
    eventlog_start_id: int
    eventlog_enabled: bool

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

        servicedesk_base_url = get_env("SERVICEDESK_BASE_URL", "").rstrip("/")
        servicedesk_login = get_env("SERVICEDESK_LOGIN", "")
        servicedesk_password = get_env("SERVICEDESK_PASSWORD", "")
        servicedesk_timeout_s = get_env_float("SERVICEDESK_TIMEOUT_S", "10")

        config_url_default = f"{web_base_url}/config"
        config_url = get_env("CONFIG_URL", config_url_default).strip() or config_url_default
        config_token = get_env("CONFIG_TOKEN", "").strip()
        config_ttl_s = get_env_float("CONFIG_TTL_S", "60")
        config_timeout_s = get_env_float("CONFIG_TIMEOUT_S", "2.5")
        config_admin_token = get_env("CONFIG_ADMIN_TOKEN", "").strip()

        database_url = get_env("DATABASE_URL", "").strip()
        database_url = normalize_database_url(database_url)

        tg_admins = tuple(parse_int_list(get_env("TG_ADMINS", "")))
        tg_users = tuple(parse_int_list(get_env("TG_USERS", "")))

        redis_url = get_env("REDIS_URL", "").strip()
        redis_socket_timeout_s = get_env_float("REDIS_SOCKET_TIMEOUT_S", "1.0")
        redis_connect_timeout_s = get_env_float("REDIS_CONNECT_TIMEOUT_S", "1.0")

        poll_interval_s = get_env_float("POLL_INTERVAL_S", "30")
        poll_max_backoff_s = get_env_float("POLL_MAX_BACKOFF_S", "300")
        min_notify_interval_s = get_env_float("MIN_NOTIFY_INTERVAL_S", "60")
        max_items_in_message = get_env_int("MAX_ITEMS_IN_MESSAGE", "10")

        obs_check_interval_s = get_env_float("OBS_CHECK_INTERVAL_S", "60")
        obs_rollback_window_s = get_env_int("OBS_ROLLBACK_WINDOW_S", "3600")
        obs_rollback_threshold = get_env_int("OBS_ROLLBACK_THRESHOLD", "3")
        admin_alert_min_interval_s = get_env_float("ADMIN_ALERT_MIN_INTERVAL_S", "300")
        obs_web_alert_min_interval_s = get_env_float("OBS_WEB_ALERT_MIN_INTERVAL_S", "300")
        obs_redis_alert_min_interval_s = get_env_float("OBS_REDIS_ALERT_MIN_INTERVAL_S", "300")
        obs_rollback_alert_min_interval_s = get_env_float("OBS_ROLLBACK_ALERT_MIN_INTERVAL_S", "300")

        eventlog_base_url = get_env("EVENTLOG_BASE_URL", servicedesk_base_url).rstrip("/")
        eventlog_poll_interval_s = get_env_int("EVENTLOG_POLL_INTERVAL_S", "600")
        eventlog_keepalive_every = get_env_int("EVENTLOG_KEEPALIVE_EVERY", "48")
        eventlog_start_id = get_env_int("EVENTLOG_START_ID", "0")
        eventlog_enabled = get_env("EVENTLOG_ENABLED", "1").strip().lower() in ("1", "true", "yes")

        return cls(
            token=token,
            web_base_url=web_base_url,
            log_level=log_level,
            web_timeout_s=web_timeout_s,
            web_cache_ttl_s=web_cache_ttl_s,
            sd_web_timeout_s=sd_web_timeout_s,
            servicedesk_base_url=servicedesk_base_url,
            servicedesk_login=servicedesk_login,
            servicedesk_password=servicedesk_password,
            servicedesk_timeout_s=servicedesk_timeout_s,
            config_url=config_url,
            config_token=config_token,
            config_ttl_s=config_ttl_s,
            config_timeout_s=config_timeout_s,
            config_admin_token=config_admin_token,
            database_url=database_url,
            tg_admins=tg_admins,
            tg_users=tg_users,
            redis_url=redis_url,
            redis_socket_timeout_s=redis_socket_timeout_s,
            redis_connect_timeout_s=redis_connect_timeout_s,
            poll_interval_s=poll_interval_s,
            poll_max_backoff_s=poll_max_backoff_s,
            min_notify_interval_s=min_notify_interval_s,
            max_items_in_message=max_items_in_message,
            obs_check_interval_s=obs_check_interval_s,
            obs_rollback_window_s=obs_rollback_window_s,
            obs_rollback_threshold=obs_rollback_threshold,
            admin_alert_min_interval_s=admin_alert_min_interval_s,
            obs_web_alert_min_interval_s=obs_web_alert_min_interval_s,
            obs_redis_alert_min_interval_s=obs_redis_alert_min_interval_s,
            obs_rollback_alert_min_interval_s=obs_rollback_alert_min_interval_s,
            eventlog_base_url=eventlog_base_url,
            eventlog_poll_interval_s=eventlog_poll_interval_s,
            eventlog_keepalive_every=eventlog_keepalive_every,
            eventlog_start_id=eventlog_start_id,
            eventlog_enabled=eventlog_enabled,
        )
