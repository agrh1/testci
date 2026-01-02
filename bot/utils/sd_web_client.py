# bot/utils/sd_web_client.py
"""
Клиент для вызова ServiceDesk-функций через наш web-сервис.

Сейчас: /sd/open (открытые заявки StatusIds=31 на стороне web).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import aiohttp


@dataclass(frozen=True)
class SdOpenResult:
    ok: bool
    status_id: int
    count_returned: int
    items: list[dict[str, Any]]
    error: Optional[str] = None
    request_id: Optional[str] = None


class SdWebClient:
    def __init__(self, base_url: str, timeout_s: float = 3.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def get_open(self, *, limit: int = 20) -> SdOpenResult:
        url = f"{self._base_url}/sd/open"
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.get(url, params={"limit": str(limit)}) as r:
                    req_id = r.headers.get("X-Request-ID")
                    # web у тебя возвращает json даже на ошибках (502) — но на всякий случай страхуемся
                    try:
                        data = await r.json()
                    except Exception:
                        txt = await r.text()
                        return SdOpenResult(
                            ok=False, status_id=31, count_returned=0, items=[],
                            error=f"Bad response (status={r.status}): {txt}",
                            request_id=req_id,
                        )

                    if r.status >= 400 or data.get("status") == "error":
                        return SdOpenResult(
                            ok=False,
                            status_id=int(data.get("status_id", 31)),
                            count_returned=0,
                            items=[],
                            error=data.get("error") or str(data),
                            request_id=req_id,
                        )

                    return SdOpenResult(
                        ok=True,
                        status_id=int(data.get("status_id", 31)),
                        count_returned=int(data.get("count_returned", 0)),
                        items=data.get("items") or [],
                        request_id=req_id,
                    )
        except Exception as e:
            return SdOpenResult(
                ok=False,
                status_id=31,
                count_returned=0,
                items=[],
                error=str(e),
                request_id=None,
            )
