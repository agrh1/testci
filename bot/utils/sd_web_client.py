# bot/utils/sd_web_client.py
"""
Клиент к нашему web-сервису для ServiceDesk-функций.
На первом шаге: получить открытые заявки через /sd/open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import aiohttp


@dataclass(frozen=True)
class SdOpenResult:
    status_id: int
    count_returned: int
    items: list[dict[str, Any]]
    error: Optional[str] = None


class SdWebClient:
    def __init__(self, base_url: str, timeout_s: float = 3.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def get_open(self, *, limit: int = 20) -> SdOpenResult:
        url = f"{self._base_url}/sd/open"
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(url, params={"limit": str(limit)}) as r:
                    data = await r.json()
                    if r.status >= 400:
                        return SdOpenResult(status_id=31, count_returned=0, items=[], error=str(data))
                    return SdOpenResult(
                        status_id=int(data.get("status_id", 31)),
                        count_returned=int(data.get("count_returned", 0)),
                        items=data.get("items") or [],
                    )
        except Exception as e:
            return SdOpenResult(status_id=31, count_returned=0, items=[], error=str(e))
