from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import requests
from flask import Flask, g, jsonify, request

app = Flask(__name__)

ALLOWED_ENVIRONMENTS = {"staging", "prod", "local"}

# Обязательные переменные для web-сервиса (readiness).
# Раньше был только SERVICEDESK_API_TOKEN, но IntraService использует Basic Auth,
# поэтому обязательными делаем логин/пароль.
REQUIRED_ENV_VARS = [
    "SERVICEDESK_BASE_URL",
    "SERVICEDESK_LOGIN",
    "SERVICEDESK_PASSWORD",
]

# Оставляем токен как "опциональный legacy" (вдруг где-то уже используется),
# но readiness больше не должен зависеть от него.
OPTIONAL_ENV_VARS = [
    "SERVICEDESK_API_TOKEN",
    "SERVICEDESK_TIMEOUT_S",
]


def get_git_sha() -> str:
    return os.getenv("GIT_SHA", "unknown")


def get_environment() -> str:
    return os.getenv("ENVIRONMENT", "unknown")


def is_strict_readiness() -> bool:
    return os.getenv("STRICT_READINESS", "0").strip() == "1"


def get_servicedesk_timeout_s() -> float:
    # Таймаут запросов к IntraService
    raw = os.getenv("SERVICEDESK_TIMEOUT_S", "10").strip()
    try:
        return float(raw)
    except Exception:
        return 10.0


# -----------------------------
# Logging
# -----------------------------

class ContextAdapter(logging.LoggerAdapter):
    """Добавляет в каждый лог ENVIRONMENT и GIT_SHA."""
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = kwargs.get("extra", {})
        extra.setdefault("environment", get_environment())
        extra.setdefault("git_sha", get_git_sha())
        kwargs["extra"] = extra
        return msg, kwargs


def setup_logging() -> ContextAdapter:
    """
    Настраивает логирование в формате key=value (удобно для grep и будущего парсинга).
    Не используем JSON, чтобы не усложнять сейчас, но формат уже “структурный”.
    """
    logger = logging.getLogger("testci.web")
    if logger.handlers:
        return ContextAdapter(logger, {})  # уже настроено (например, при reload)

    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()

    formatter = logging.Formatter(
        fmt=(
            "ts=%(asctime)s level=%(levelname)s service=web "
            "env=%(environment)s sha=%(git_sha)s "
            "msg=%(message)s"
        )
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False

    return ContextAdapter(logger, {})


log = setup_logging()


def _get_request_id() -> str:
    rid = request.headers.get("X-Request-ID")
    if rid and rid.strip():
        return rid.strip()
    return uuid.uuid4().hex


@app.before_request
def before_request() -> None:
    """
    Перед запросом:
    - генерим request_id
    - запоминаем start_time
    """
    g.request_id = _get_request_id()
    g.start_time = time.perf_counter()


@app.after_request
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

    log.info(
        "request method=%s path=%s status=%s duration_ms=%s request_id=%s remote=%s",
        request.method,
        request.path,
        getattr(response, "status_code", "unknown"),
        duration_ms,
        getattr(g, "request_id", "unknown"),
        request.headers.get("X-Forwarded-For", request.remote_addr),
    )
    return response


# -----------------------------
# Readiness checks
# -----------------------------

@dataclass(frozen=True)
class ReadyCheck:
    name: str
    ok: bool
    detail: str


def _missing_required_env() -> list[str]:
    missing: list[str] = []
    for key in REQUIRED_ENV_VARS:
        value = os.getenv(key)
        if value is None or value.strip() == "":
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


def build_readiness_checks() -> list[ReadyCheck]:
    strict = is_strict_readiness()
    return [
        _check_environment(strict),
        _check_required_env(strict),
    ]


# -----------------------------
# IntraService интеграция
# -----------------------------

def _sd_base_url() -> str:
    return os.getenv("SERVICEDESK_BASE_URL", "").rstrip("/")


def _sd_login() -> str:
    return os.getenv("SERVICEDESK_LOGIN", "")


def _sd_password() -> str:
    return os.getenv("SERVICEDESK_PASSWORD", "")


def _intraservice_list_tasks_by_status(*, status_id: int, page: int, pagesize: int, fields: str) -> dict[str, Any]:
    """
    Запрос списка заявок в IntraService по статусу.

    ВАЖНО:
    - IntraService использует Basic Auth (login/password).
    - Ответ коллекции содержит Tasks + Paginator.
    """
    base_url = _sd_base_url()
    url = f"{base_url}/api/task"

    params = {
        "StatusIds": str(status_id),
        "page": str(page),
        "pagesize": str(pagesize),
        "fields": fields,
    }

    timeout_s = get_servicedesk_timeout_s()

    # Прокидываем request_id для корреляции логов (если IntraService его сохранит — отлично).
    headers = {
        "Accept": "application/json",
        "X-Request-ID": getattr(g, "request_id", uuid.uuid4().hex),
    }

    r = requests.get(
        url,
        params=params,
        auth=(_sd_login(), _sd_password()),
        timeout=timeout_s,
        headers=headers,
    )

    if r.status_code >= 400:
        # Возвращаем текст как есть, чтобы было диагностируемо.
        raise RuntimeError(f"IntraService error {r.status_code}: {r.text}")

    return r.json()


# -----------------------------
# Routes
# -----------------------------

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


@app.get("/sd/open")
def sd_open() -> tuple[Any, int]:
    """
    Возвращает заявки IntraService в статусе "Открыта" (StatusId=31).

    query params:
    - limit: сколько заявок вернуть суммарно (1..500), по умолчанию 50
    - pagesize: размер страницы IntraService (1..2000), по умолчанию 50
    - fields: список полей IntraService через запятую (умолчание: Id,Name,Created,Creator,StatusId)
    """
    status_id = 31

    # Защита от "случайно вернуть слишком много"
    try:
        limit = int(request.args.get("limit", "50"))
    except Exception:
        limit = 50
    limit = max(1, min(limit, 500))

    try:
        pagesize = int(request.args.get("pagesize", "50"))
    except Exception:
        pagesize = 50
    pagesize = max(1, min(pagesize, 2000))

    fields = (request.args.get("fields") or "Id,Name,Created,Creator,StatusId").strip()

    items: list[dict[str, Any]] = []
    page = 1
    paginator: dict[str, Any] | None = None

    try:
        while len(items) < limit:
            data = _intraservice_list_tasks_by_status(
                status_id=status_id,
                page=page,
                pagesize=pagesize,
                fields=fields,
            )

            tasks = data.get("Tasks") or []
            paginator = data.get("Paginator") or {}

            items.extend(tasks)

            # Если страницы кончились — выходим
            page_count = int(paginator.get("PageCount", page))
            if page >= page_count:
                break
            page += 1

        items = items[:limit]

        return jsonify(
            {
                "status_id": status_id,
                "count_returned": len(items),
                "items": items,
                "paginator": paginator,
            }
        ), 200

    except Exception as e:
        # Важно: не 500 без деталей. Это endpoint для интеграции, диагностика важна.
        log.exception("sd_open failed request_id=%s err=%s", getattr(g, "request_id", "unknown"), str(e))
        return jsonify(
            {
                "status": "error",
                "error": str(e),
                "request_id": getattr(g, "request_id", "unknown"),
            }
        ), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
