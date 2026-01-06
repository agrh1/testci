"""
Health/ready endpoints.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from flask import Blueprint, current_app, g, jsonify, request

from web.settings import (
    ALLOWED_ENVIRONMENTS,
    REQUIRED_ENV_VARS,
    get_environment,
    get_git_sha,
    is_strict_readiness,
)

bp = Blueprint("health", __name__)


@dataclass(frozen=True)
class ReadyCheck:
    name: str
    ok: bool
    detail: str


def _get_request_id() -> str:
    rid = request.headers.get("X-Request-ID")
    if rid and rid.strip():
        return rid.strip()
    return uuid.uuid4().hex


@bp.before_app_request
def before_request() -> None:
    """
    Перед запросом:
    - генерим request_id
    - запоминаем start_time
    """
    g.request_id = _get_request_id()
    g.start_time = time.perf_counter()


@bp.after_app_request
def after_request(response: Any) -> Any:
    """
    После запроса:
    - добавляем X-Request-ID
    - пишем одну строку access log
    """
    try:
        duration_ms = int((time.perf_counter() - g.start_time) * 1000)
    except Exception:
        duration_ms = -1

    response.headers["X-Request-ID"] = getattr(g, "request_id", "unknown")

    logger = current_app.config.get("APP_LOGGER", current_app.logger)
    logger.info(
        "request method=%s path=%s status=%s duration_ms=%s request_id=%s remote=%s",
        request.method,
        request.path,
        getattr(response, "status_code", "unknown"),
        duration_ms,
        getattr(g, "request_id", "unknown"),
        request.headers.get("X-Forwarded-For", request.remote_addr),
    )
    return response


def _missing_required_env() -> list[str]:
    missing: list[str] = []
    for key in REQUIRED_ENV_VARS:
        value = current_app.config.get(key) or ""
        if not str(value).strip():
            missing.append(key)
    return missing


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


def _check_required_env(strict: bool) -> ReadyCheck:
    missing = _missing_required_env()

    if strict:
        ok = len(missing) == 0
        detail = "Все обязательные переменные заданы" if ok else f"Не заданы: {', '.join(missing)}"
        return ReadyCheck(name="config.required_env", ok=ok, detail=detail)

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


def _build_readiness_checks() -> list[ReadyCheck]:
    strict = is_strict_readiness()
    return [
        _check_environment(strict),
        _check_required_env(strict),
    ]


@bp.get("/")
def index() -> tuple[str, int]:
    return "testCI service is running", 200


@bp.get("/health")
def health() -> tuple[Any, int]:
    return jsonify({"status": "ok"}), 200


@bp.get("/ready")
def ready() -> tuple[Any, int]:
    checks = _build_readiness_checks()
    all_ok = all(c.ok for c in checks)

    payload = {
        "status": "ok" if all_ok else "not_ready",
        "ready": all_ok,
        "strict": is_strict_readiness(),
        "checks": [asdict(c) for c in checks],
    }
    return jsonify(payload), 200 if all_ok else 503


@bp.get("/status")
def status() -> tuple[Any, int]:
    payload = {
        "status": "ok",
        "environment": get_environment(),
        "git_sha": get_git_sha(),
    }
    return jsonify(payload), 200
