"""
Основное веб-приложение.

Содержит:
- Flask-приложение `app`
- два HTTP-эндпоинта:
  - GET /health — healthcheck для Docker/Watchtower/балансировщика
  - GET /       — простая страница, по которой удобно проверять CD
"""

from flask import Flask, Response, jsonify

# Создаём экземпляр приложения Flask.
# Имя модуля (__name__) нужно Flask'у для поиска шаблонов, статики и т.п.
app = Flask(__name__)


@app.get("/health")
def health() -> Response:
    """
    Healthcheck эндпоинт.

    Используется:
    - Docker healthcheck (см. docker-compose.yml)
    - Watchtower, балансировщики, мониторинг.

    Возвращает JSON с простым статусом.
    """
    payload = {"status": "ok"}
    # jsonify сам выставит правильный Content-Type и сериализует в JSON
    return jsonify(payload), 200


@app.get("/")
def index() -> tuple[str, int]:
    """
    Простой индексный эндпоинт.

    Здесь удобно менять текст, чтобы визуально подтверждать,
    что новая версия доехала до сервера (CI/CD работает).
    """
    return "Hello from CD v4 (PROD CHECK)", 200


if __name__ == "__main__":
    # Этот блок используется только при локальном запуске:
    #   python app.py
    #
    # В Docker мы запускаем приложение через gunicorn
    # (это настраивается в Dockerfile командой CMD).
    app.run(host="0.0.0.0", port=8000)
