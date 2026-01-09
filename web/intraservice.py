# web/intraservice.py
"""
Интеграция с IntraService API.

Что важно из документации:
- Авторизация Basic Auth (логин/пароль). :contentReference[oaicite:3]{index=3}
- Список заявок: GET /api/task?...&pagesize=&page= :contentReference[oaicite:4]{index=4}
- Коллекции постранично + Paginator в ответе. :contentReference[oaicite:5]{index=5}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import requests
from flask import current_app


@dataclass(frozen=True)
class IntraServiceConfig:
    base_url: str
    login: str
    password: str
    timeout_s: float = 10.0


def _cfg_from_env() -> IntraServiceConfig:
    # Используем существующую идеологию проекта: конфиг через ENV.
    base_url = current_app.config["SERVICEDESK_BASE_URL"].rstrip("/")
    login = current_app.config["SERVICEDESK_LOGIN"]
    password = current_app.config["SERVICEDESK_PASSWORD"]
    timeout_s = float(current_app.config.get("SERVICEDESK_TIMEOUT_S", 10.0))
    return IntraServiceConfig(base_url=base_url, login=login, password=password, timeout_s=timeout_s)


def list_tasks_by_status(
    *,
    status_id: int,
    page: int,
    pagesize: int,
    fields: str,
    include: Optional[str] = None,
    sort: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    """
    Возвращает сырой JSON IntraService для /api/task.

    Фильтрация по полям выполняется через query-параметры (в документации это {filterFields}). :contentReference[oaicite:6]{index=6}
    """
    cfg = _cfg_from_env()

    url = f"{cfg.base_url}/api/task"
    params = {
        "StatusIds": str(status_id),
        "page": str(page),
        "pagesize": str(pagesize),
        "fields": fields,
    }
    if include:
        params["include"] = include
    if sort:
        params["sort"] = sort

    headers = {"Accept": "application/json"}
    if request_id:
        headers["X-Request-ID"] = request_id

    r = requests.get(
        url,
        params=params,
        auth=(cfg.login, cfg.password),  # Basic Auth :contentReference[oaicite:7]{index=7}
        timeout=cfg.timeout_s,
        headers=headers,
    )

    # IntraService при ошибке может возвращать json с Message/MessageDetail. :contentReference[oaicite:8]{index=8}
    if r.status_code >= 400:
        raise RuntimeError(f"IntraService error {r.status_code}: {r.text}")

    return r.json()
