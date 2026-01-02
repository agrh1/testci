# bot/utils/polling.py
"""
Фоновая задача (polling) с безопасной деградацией + backoff.

Теперь "работа" не заглушка:
- делаем HTTP GET к web (например /ready)
- ошибки ловим и логируем
- при ошибках увеличиваем интервал (exp backoff) до max_backoff_s
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp


@dataclass
class PollingState:
    runs: int = 0
    failures: int = 0
    last_success_ts: Optional[float] = None
    last_error: Optional[str] = None
    last_duration_ms: Optional[int] = None
    last_http_status: Optional[int] = None


async def polling_loop(
    *,
    state: PollingState,
    stop_event: asyncio.Event,
    url: str,
    base_interval_s: float = 30.0,
    timeout_s: float = 2.0,
    max_backoff_s: float = 300.0,
    logger_name: str = "bot.polling",
) -> None:
    """
    Периодически вызывает URL.

    Backoff:
    - при успехе: интервал = base_interval_s
    - при ошибке: интервал *= 2 (до max_backoff_s)
    """
    interval_s = base_interval_s

    timeout = aiohttp.ClientTimeout(total=timeout_s)

    # Важно: одна сессия на цикл, чтобы не создавать TCP заново на каждый запрос
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while not stop_event.is_set():
            t0 = time.perf_counter()
            state.runs += 1

            try:
                async with session.get(url) as r:
                    await r.release()
                    state.last_http_status = r.status

                    if 200 <= r.status < 300:
                        state.last_success_ts = time.time()
                        state.last_error = None
                        interval_s = base_interval_s
                    else:
                        # HTTP не-2xx — это тоже ошибка с точки зрения polling
                        state.failures += 1
                        state.last_error = f"HTTP {r.status}"
                        interval_s = min(max_backoff_s, max(base_interval_s, interval_s * 2))

                state.last_duration_ms = int((time.perf_counter() - t0) * 1000)

            except Exception as e:
                state.failures += 1
                state.last_http_status = None
                state.last_error = str(e)
                state.last_duration_ms = int((time.perf_counter() - t0) * 1000)
                interval_s = min(max_backoff_s, max(base_interval_s, interval_s * 2))

            # Спим до следующего цикла, но реагируем на stop_event
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
            except asyncio.TimeoutError:
                pass
