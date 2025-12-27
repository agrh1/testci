# tests/test_bot.py
# Unit-тесты логики бота (без Telegram и сети).
#
# Важно: тестируем только "чистые" элементы:
# - текст ответа /ping
# - корректную сборку HEALTH_URL из WEB_BASE_URL

import importlib
import os


def _reload_bot_with_env(web_base_url: str):
    """
    Перезагружаем модуль bot с заданной переменной окружения WEB_BASE_URL.
    Это нужно, потому что HEALTH_URL вычисляется при импорте модуля.
    """
    os.environ["WEB_BASE_URL"] = web_base_url

    import bot  # импортируем после установки env

    return importlib.reload(bot)


def test_ping_reply_text():
    """Проверяем фиксированный ответ команды /ping."""
    import bot

    assert bot.ping_reply_text() == "pong ✅"


def test_health_url_from_web_base_url_without_slash():
    """HEALTH_URL должен оканчиваться на /health, даже если WEB_BASE_URL без '/'."""
    bot = _reload_bot_with_env("http://web:8000")
    assert bot.HEALTH_URL == "http://web:8000/health"


def test_health_url_from_web_base_url_with_slash():
    """HEALTH_URL должен быть корректным, если WEB_BASE_URL с завершающим '/'."""
    bot = _reload_bot_with_env("http://web:8000/")
    assert bot.HEALTH_URL == "http://web:8000/health"
