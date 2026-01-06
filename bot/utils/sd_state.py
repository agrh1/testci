# bot/utils/sd_state.py
from __future__ import annotations

import hashlib
import json
import os
from typing import Any


def normalize_tasks_for_message(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Нормализация для отображения пользователю:
    - сортируем по Id
    - берём Id, Name, Creator (строка) и ссылку на заявку
    """
    base_url = (os.getenv("SERVICEDESK_BASE_URL", "").strip() or "https://support.pixel.org.ru").rstrip("/")
    return sorted(
        (
            {
                "Id": int(t.get("Id", 0)),
                "Name": str(t.get("Name", "")),
                "Creator": str(t.get("Creator", "")),
                "Url": f"{base_url}/task/view/{int(t.get('Id', 0))}",
            }
            for t in items
            if int(t.get("Id", 0)) > 0
        ),
        key=lambda x: x["Id"],
    )


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
    ids = sorted({int(t.get("Id", 0)) for t in items if int(t.get("Id", 0)) > 0})
    payload = json.dumps(ids, ensure_ascii=False, separators=(",", ":"))
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return h, ids
