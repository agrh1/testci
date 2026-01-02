# intraservice_client.py
# Клиент IntraService API (aiohttp), только то что нужно для шага: получить заявки со StatusId=31.
# Комментарии на русском, максимально приземлённо для сопровождения.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import aiohttp


@dataclass(frozen=True)
class Paginator:
    count: int
    page: int
    page_count: int
    page_size: int
    count_on_page: int


@dataclass(frozen=True)
class TaskShort:
    id: int
    name: str
    created: Optional[str] = None
    creator: Optional[str] = None
    service_id: Optional[int] = None
    priority_id: Optional[int] = None
    status_id: Optional[int] = None


class IntraServiceClient:
    """
    Мини-клиент для IntraService.
    Авторизация: Basic Auth (логин/пароль), как в документации. :contentReference[oaicite:7]{index=7}
    """

    def __init__(
        self,
        base_url: str,
        login: str,
        password: str,
        session: aiohttp.ClientSession,
        timeout_s: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = aiohttp.BasicAuth(login=login, password=password)
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def list_tasks_by_status(
        self,
        status_id: int,
        page: int = 1,
        page_size: int = 50,
        fields: str = "Id,Name,Created,Creator,ServiceId,PriorityId,StatusId",
    ) -> tuple[list[TaskShort], Paginator]:
        """
        Получить список заявок с фильтром по статусу.
        IntraService пагинирует через page/pagesize + Paginator в ответе. :contentReference[oaicite:8]{index=8}
        """

        url = f"{self._base_url}/api/task"
        params = {
            "StatusId": str(status_id),
            "fields": fields,
            "page": str(page),
            "pagesize": str(page_size),
        }

        async with self._session.get(
            url,
            params=params,
            auth=self._auth,
            timeout=self._timeout,
            headers={"Accept": "application/json"},
        ) as resp:
            # Если авторизация/права/URL неверные — тут будет понятная диагностика
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"IntraService API error {resp.status}: {text}")

            data: dict[str, Any] = await resp.json()

        tasks_raw = data.get("Tasks") or []
        paginator_raw = data.get("Paginator") or {}

        tasks: list[TaskShort] = []
        for t in tasks_raw:
            # IntraService отдаёт поля с такими именами, если ты их запросил через fields.
            tasks.append(
                TaskShort(
                    id=int(t.get("Id")),
                    name=str(t.get("Name", "")),
                    created=t.get("Created"),
                    creator=t.get("Creator"),
                    service_id=t.get("ServiceId"),
                    priority_id=t.get("PriorityId"),
                    status_id=t.get("StatusId"),
                )
            )

        paginator = Paginator(
            count=int(paginator_raw.get("Count", 0)),
            page=int(paginator_raw.get("Page", page)),
            page_count=int(paginator_raw.get("PageCount", 1)),
            page_size=int(paginator_raw.get("PageSize", page_size)),
            count_on_page=int(paginator_raw.get("CountOnPage", len(tasks))),
        )

        return tasks, paginator
