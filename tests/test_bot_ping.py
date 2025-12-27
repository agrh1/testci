# tests/test_bot_ping.py
# Smoke-тест: базовая логика ответа /ping

from bot import ping_reply_text


def test_ping_reply_text():
    assert ping_reply_text().startswith("pong")
