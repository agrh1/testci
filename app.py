"""
app.py — web-сервис (Flask).

Эндпоинты:
- GET /health  -> {"status": "ok"}   (для healthcheck'ов и мониторинга)
- GET /status  -> {"status":"ok", "git_sha":"..."} (чтобы видеть версию релиза)
"""

import os

from flask import Flask, Response, jsonify

# Создаём экземпляр приложения Flask.
# Имя модуля (__name__) нужно Flask'у для поиска шаблонов, статики и т.п.
app = Flask(__name__)

def get_git_sha() -> str:
    """
    Берём SHA коммита из переменной окружения.
    Она задаётся на этапе сборки Docker-образа через ARG/ENV.
    """
    return os.getenv("GIT_SHA", "unknown").strip() or "unknown"

@app.get("/health")
def health() -> Response:
    """Минимальный health endpoint: нужен для docker healthcheck и проверок."""
    return jsonify(status="ok")

@app.get("/status")
def status() -> Response:
    """
    Диагностический endpoint: показывает, какая версия (коммит) сейчас запущена.
    Это удобно для staging/prod, чтобы не гадать, обновилось ли окружение.
    """
    return jsonify(status="ok", git_sha=get_git_sha())



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
