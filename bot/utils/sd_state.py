"""Утилиты для нормализации данных ServiceDesk и вычисления снэпшотов."""

# bot/utils/sd_state.py
from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional


def _to_int(value: object) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def normalize_tasks_for_message(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Нормализация для отображения пользователю:
    - берём Id, Name, Creator, Created, ServiceId/ServiceCode/ServiceName и ссылку
    - порядок сохраняем как пришёл от API
    """
    base_url = (os.getenv("SERVICEDESK_BASE_URL", "").strip())
    normalized: list[dict[str, Any]] = []
    for t in items:
        tid = _to_int(t.get("Id"))
        if tid is None or tid <= 0:
            continue
        service_id = _to_int(t.get("ServiceId"))
        normalized.append(
            {
                "Id": tid,
                "Name": str(t.get("Name", "")),
                "Creator": str(t.get("Creator", "")),
                "Created": str(t.get("Created", "")),
                "ServiceId": service_id,
                "ServiceCode": str(t.get("ServiceCode", "")),
                "ServiceName": str(t.get("ServiceName", "")),
                "Url": f"{base_url}/task/view/{tid}",
            }
        )

    return normalized


def make_ids_snapshot_hash(items: list[dict[str, Any]]) -> tuple[str, list[int]]:
    """
    Снэпшот ТОЛЬКО по составу очереди:
    - берём только Id
    - сортируем
    - считаем sha256

    Возвращаем:
    - hash
    - ids (отсортированный список) — полезно для диагностики
    """
    ids_set: set[int] = set()
    for t in items:
        tid = _to_int(t.get("Id"))
        if tid is None or tid <= 0:
            continue
        ids_set.add(tid)

    ids = sorted(ids_set)
    payload = json.dumps(ids, ensure_ascii=False, separators=(",", ":"))
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return h, ids
