# tests/test_health.py
# Smoke-тест: web-сервис должен отвечать на /health

import json
import os
import time
import urllib.request


def test_health_endpoint_ok():
    """
    Проверяем, что /health отвечает HTTP 200
    и возвращает JSON со status=ok.

    По умолчанию тест ходит на localhost:8000,
    но можно переопределить переменной WEB_TEST_URL.
    """
    url = os.getenv("WEB_TEST_URL", "http://localhost:8000/health")

    # Небольшой retry, чтобы удобно было гонять тест сразу после поднятия контейнеров
    last_err = None
    for _ in range(10):
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                assert resp.status == 200
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                assert data.get("status") == "ok"
                return
        except Exception as e:
            last_err = e
            time.sleep(1)

    raise AssertionError(f"/health is not ready: {last_err}")
