from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from flask import Flask, jsonify

app = Flask(__name__)

ALLOWED_ENVIRONMENTS = {"staging", "prod", "local"}

# Список обязательных переменных для "боевой" готовности.
# Сейчас это задел под будущую интеграцию с сервис-деском.
# Можно расширять без изменения кода проверок.
REQUIRED_ENV_VARS = [
    # Базовый URL сервис-деска (пример: https://servicedesk.company.local/api)
    "SERVICEDESK_BASE_URL",
    # Токен/ключ для доступа к API сервис-деска
    "SERVICEDESK_API_TOKEN",
]


def get_git_sha() -> str:
    return os.getenv("GIT_SHA", "unknown")


def get_environment() -> str:
    return os.getenv("ENVIRONMENT", "unknown")


def is_strict_readiness() -> bool:
    return os.getenv("STRICT_READINESS", "0").strip() == "1"


@dataclass(frozen=True)
class ReadyCheck:
    name: str
    ok: bool
    detail: str


def _check_environment(strict: bool) -> ReadyCheck:
    env = get_environment()

    if strict:
        ok = env in ALLOWED_ENVIRONMENTS
        detail = (
            f"ENVIRONMENT={env} (ожидается одно из: {', '.join(sorted(ALLOWED_ENVIRONMENTS))})"
            if not ok
            else f"ENVIRONMENT={env}"
        )
        return ReadyCheck(name="env.environment", ok=ok, detail=detail)

    # Нестрогий режим: предупреждаем, но не блокируем готовность.
    if env not in ALLOWED_ENVIRONMENTS:
        return ReadyCheck(
            name="env.environment",
            ok=True,
            detail=(
                f"ENVIRONMENT={env} (предупреждение: рекомендуется одно из "
                f"{', '.join(sorted(ALLOWED_ENVIRONMENTS))}; строгий режим включается STRICT_READINESS=1)"
            ),
        )
    return ReadyCheck(name="env.environment", ok=True, detail=f"ENVIRONMENT={env}")


def _missing_required_env() -> list[str]:
    missing: list[str] = []
    for key in REQUIRED_ENV_VARS:
        value = os.getenv(key)
        if value is None or value.strip() == "":
            missing.append(key)
    return missing


def _check_required_env(strict: bool) -> ReadyCheck:
    missing = _missing_required_env()

    if strict:
        ok = len(missing) == 0
        detail = "Все обязательные переменные заданы" if ok else f"Не заданы: {', '.join(missing)}"
        return ReadyCheck(name="config.required_env", ok=ok, detail=detail)

    # Нестрогий режим: предупреждение, но ok=True
    if missing:
        return ReadyCheck(
            name="config.required_env",
            ok=True,
            detail=(
                f"Предупреждение: не заданы {', '.join(missing)} "
                f"(в строгом режиме STRICT_READINESS=1 это будет not ready)"
            ),
        )
    return ReadyCheck(name="config.required_env", ok=True, detail="Все обязательные переменные заданы")


def build_readiness_checks() -> list[ReadyCheck]:
    strict = is_strict_readiness()
    return [
        _check_environment(strict),
        _check_required_env(strict),
    ]


@app.get("/")
def index() -> tuple[str, int]:
    return "testCI service is running", 200


@app.get("/health")
def health() -> tuple[Any, int]:
    return jsonify({"status": "ok"}), 200


@app.get("/ready")
def ready() -> tuple[Any, int]:
    checks = build_readiness_checks()
    all_ok = all(c.ok for c in checks)

    payload = {
        "status": "ok" if all_ok else "not_ready",
        "ready": all_ok,
        "strict": is_strict_readiness(),
        "checks": [asdict(c) for c in checks],
    }
    return jsonify(payload), 200 if all_ok else 503


@app.get("/status")
def status() -> tuple[Any, int]:
    payload = {
        "status": "ok",
        "environment": get_environment(),
        "git_sha": get_git_sha(),
    }
    return jsonify(payload), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
