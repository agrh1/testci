"""
Web-сервис (Flask).

Шаг 12:
- Добавляем ENVIRONMENT=staging|prod|local (или unknown) и показываем его в /status.

Требования тестов:
- Должен существовать endpoint "/" и возвращать 200 + непустой текст.
"""

from __future__ import annotations

import os
from typing import Any

from flask import Flask, jsonify

app = Flask(__name__)


def get_git_sha() -> str:
    """Возвращает git sha, проброшенный в контейнер на этапе сборки."""
    return os.getenv("GIT_SHA", "unknown")


def get_environment() -> str:
    """
    Возвращает окружение.

    Ожидаемые значения: staging | prod | local.
    Если не задано — unknown (чтобы не падать).
    """
    return os.getenv("ENVIRONMENT", "unknown")


@app.get("/")
def index() -> tuple[str, int]:
    """
    Простой текстовый endpoint.

    Нужен:
    - для smoke-проверок руками (curl в браузере)
    - для unit-тестов проекта (ожидают 200 и непустой текст)
    """
    return "Hello from CD v5 (PROD CHECK)", 200


@app.get("/health")
def health() -> tuple[Any, int]:
    """Liveness: процесс жив."""
    return jsonify({"status": "ok"}), 200


@app.get("/status")
def status() -> tuple[Any, int]:
    """Диагностический endpoint: версия + окружение."""
    payload = {
        "status": "ok",
        "environment": get_environment(),
        "git_sha": get_git_sha(),
    }
    return jsonify(payload), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
