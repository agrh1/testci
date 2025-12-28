from __future__ import annotations

import json
import os
import time
import urllib.request

import pytest

from app import app


def test_health_unit_ok() -> None:
    """
    Unit-тест: проверяем /health через Flask test client.

    Это должно стабильно работать в CI, потому что не нужен поднятый сервер.
    """
    client = app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200

    data = resp.get_json()
    assert data is not None
    assert data.get("status") == "ok"


@pytest.mark.skipif(not os.getenv("WEB_TEST_URL"), reason="WEB_TEST_URL не задан — integration-тест пропущен")
def test_health_integration_ok() -> None:
    """
    Integration-тест: проверяем /health по реальному HTTP URL.

    Запускается только если задан WEB_TEST_URL.
    Пример:
      WEB_TEST_URL=http://localhost:8000/health pytest -q
    """
    url = os.getenv("WEB_TEST_URL", "").strip()

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
