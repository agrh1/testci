# bot/utils/config_client.py

"""Клиент для получения динамического конфига бота из web-сервиса (/config).

Идея
----
- Web хранит конфиг в Postgres и отдаёт его через HTTP.
- Bot читает конфиг через HTTP, НЕ подключается к БД напрямую.
- Bot использует TTL-кэш и fallback на последний успешный конфиг, чтобы
  временная недоступность web не приводила к падению бота.

Переменные окружения (bot)
-------------------------
CONFIG_URL      - полный URL до /config (по умолчанию {WEB_BASE_URL}/config)
CONFIG_TOKEN    - если на web включена защита, бот передаёт его заголовком
                  X-Config-Token
CONFIG_TTL_S    - TTL кэша (сек), по умолчанию 60
CONFIG_TIMEOUT_S- таймаут HTTP запроса, по умолчанию 2.5

Формат ответа web (/config)
--------------------------
Ожидаем JSON-объект. Важно только, что там есть:
- version (int)
- routing {...}
- escalation {...}

Содержимое routing/escalation валидирует и парсит отдельный слой (RuntimeConfig).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Tuple

import aiohttp


@dataclass(frozen=True)
class ConfigFetchResult:
    """Результат попытки получения конфига."""

    ok: bool
    status: Optional[int]
    error: Optional[str]
    duration_ms: int
    request_id: str
    data: Optional[dict[str, Any]]


class ConfigClient:
    """HTTP-клиент для /config с TTL-кэшированием и fallback."""

    def __init__(
        self,
        *,
        url: str,
        token: str = "",
        timeout_s: float = 2.5,
        cache_ttl_s: float = 60.0,
    ) -> None:
        self.url = url
        self.token = token.strip()
        self.timeout_s = timeout_s
        self.cache_ttl_s = cache_ttl_s

        # cache: (ts, data)
        self._cache: Optional[Tuple[float, dict[str, Any]]] = None
        self._lock = asyncio.Lock()

    async def _fetch(self, request_id: str) -> ConfigFetchResult:
        t0 = time.perf_counter()
        timeout = aiohttp.ClientTimeout(total=self.timeout_s)

        headers = {"X-Request-ID": request_id}
        if self.token:
            headers["X-Config-Token"] = self.token

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self.url, headers=headers) as r:
                    status = r.status
                    # читаем JSON; если там не JSON, получим исключение
                    data = await r.json(content_type=None)
                    dt = int((time.perf_counter() - t0) * 1000)
                    ok = 200 <= status < 300 and isinstance(data, dict)
                    if not ok:
                        return ConfigFetchResult(
                            ok=False,
                            status=status,
                            error=f"bad status or payload (status={status})",
                            duration_ms=dt,
                            request_id=request_id,
                            data=None,
                        )
                    return ConfigFetchResult(
                        ok=True,
                        status=status,
                        error=None,
                        duration_ms=dt,
                        request_id=request_id,
                        data=data,
                    )
        except Exception as e:
            dt = int((time.perf_counter() - t0) * 1000)
            return ConfigFetchResult(
                ok=False,
                status=None,
                error=str(e),
                duration_ms=dt,
                request_id=request_id,
                data=None,
            )

    async def get(self, *, force: bool = False) -> ConfigFetchResult:
        """Возвращает конфиг.

        Поведение:
        - если TTL не истёк и force=False -> возвращаем кэш
        - иначе -> делаем HTTP запрос
        - при ошибке HTTP -> возвращаем последнюю успешную версию из кэша
        """
        now = time.time()

        async with self._lock:
            if not force and self._cache is not None:
                ts, cached = self._cache
                if (now - ts) <= self.cache_ttl_s:
                    return ConfigFetchResult(
                        ok=True,
                        status=200,
                        error=None,
                        duration_ms=0,
                        request_id="cache",
                        data=cached,
                    )

            request_id = str(uuid.uuid4())
            res = await self._fetch(request_id=request_id)
            if res.ok and res.data is not None:
                self._cache = (now, res.data)
                return res

            # fallback на кэш, если он есть
            if self._cache is not None:
                _ts, cached = self._cache
                return ConfigFetchResult(
                    ok=True,
                    status=res.status,
                    error=f"fetch failed, using cached: {res.error}",
                    duration_ms=res.duration_ms,
                    request_id=res.request_id,
                    data=cached,
                )

            return res
