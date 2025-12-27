# tests/test_bot.py
# Unit-тесты логики бота (без Telegram и сети)

from bot import build_health_url, ping_reply_text


def test_build_health_url_simple():
    assert build_health_url("http://web:8000") == "http://web:8000/health"


def test_build_health_url_with_trailing_slash():
    assert build_health_url("http://web:8000/") == "http://web:8000/health"


def test_ping_reply_text():
    assert ping_reply_text() == "pong ✅"
