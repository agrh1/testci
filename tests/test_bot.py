"""
Тесты для вспомогательных функций в bot.py.

Важно:
- мы НЕ тестируем бесконечный цикл main_loop (это не unit-тест, а уже e2e),
- вместо этого покрываем чистые функции: build_health_url и read_config_from_env.

Тесты для вспомогательных функций в bot.py.
"""
import sys
from pathlib import Path

# --- Добавляем корень проекта в sys.path ------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# -----------------------------------------------------------------------------

import os

from bot import (
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_WEB_HOST,
    DEFAULT_WEB_PORT,
    build_health_url,
    read_config_from_env,
)


def test_build_health_url() -> None:
    """Проверяем, что URL собирается корректно."""
    url = build_health_url("example.com", 1234)
    assert url == "http://example.com:1234/health"


def test_read_config_from_env_defaults(monkeypatch) -> None:
    """
    Если переменные окружения не заданы, должны вернуться значения по умолчанию.
    """
    # Чистим переменные окружения, чтобы не влияли на тест.
    for key in ("WEB_HOST", "WEB_PORT", "BOT_INTERVAL"):
        monkeypatch.delenv(key, raising=False)

    host, port, interval = read_config_from_env()

    assert host == DEFAULT_WEB_HOST
    assert port == DEFAULT_WEB_PORT
    assert interval == DEFAULT_INTERVAL_SECONDS


def test_read_config_from_env_custom_values(monkeypatch) -> None:
    """
    Если переменные окружения заданы, должны использоваться они.
    """
    monkeypatch.setenv("WEB_HOST", "custom-host")
    monkeypatch.setenv("WEB_PORT", "9000")
    monkeypatch.setenv("BOT_INTERVAL", "42")

    host, port, interval = read_config_from_env()

    assert host == "custom-host"
    assert port == 9000
    assert interval == 42
