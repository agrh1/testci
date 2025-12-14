"""
Тесты для веб-приложения (app.py).

Проверяем:
- что эндпоинт /health отвечает 200 и возвращает ожидаемый JSON;
- что эндпоинт / отвечает 200 и содержит ожидаемый текст.

Этот набор тестов, конечно, минимальный, но он уже:
- позволяет поймать самые грубые ошибки (например, приложение не стартует);
- служит основой, чтобы постепенно покрывать бизнес-логику.


Тесты для веб-приложения (app.py).
"""
import sys
from pathlib import Path

# --- Добавляем корень проекта в sys.path ------------------------------------
# Файл лежит в testCI/tests/test_app.py
# Родительская папка два уровня вверх — это корень проекта (где лежит app.py).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# -----------------------------------------------------------------------------

from app import app


def test_health_endpoint_returns_ok() -> None:
    """Проверяем, что /health возвращает статус 200 и правильный JSON."""
    client = app.test_client()
    response = client.get("/health")

    assert response.status_code == 200
    assert response.is_json
    data = response.get_json()
    assert isinstance(data, dict)
    assert data.get("status") == "ok"


def test_index_endpoint_returns_text() -> None:
    """Проверяем, что / возвращает статус 200 и не пустой текст."""
    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    # content-type может быть text/html; charset=utf-8 — нам важнее содержимое
    text = response.get_data(as_text=True)
    assert "Hello from CD v4 (PROD CHECK)" in text
