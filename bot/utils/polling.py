# bot/utils/polling.py
"""
Фоновая задача (polling) с безопасной деградацией.

Паттерн:
- одна задача крутится в цикле, периодически выполняет работу
- исключения ловятся, записываются в состояние, бот не падает
- есть stop_event для корректного завершения
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class PollingState:
    runs: int = 0
    failures: int = 0
    last_success_ts: Optional[float] = None
    last_error: Optional[str] = None
    last_duration_ms: Optional[int] = None


async def polling_loop(
    *,
    state: PollingState,
    stop_event: asyncio.Event,
    interval_s: float = 30.0,
) -> None:
    """
    Основной цикл polling.

    Сейчас "работа" — заглушка. На шаге с ServiceDesk заменим на реальные вызовы.
    """
    while not stop_event.is_set():
        t0 = time.perf_counter()
        state.runs += 1
        try:
            # TODO: здесь будет реальный опрос сервис-деска
            # Пока имитируем успешную работу
            await asyncio.sleep(0)  # yield

            state.last_success_ts = time.time()
            state.last_error = None
            state.last_duration_ms = int((time.perf_counter() - t0) * 1000)
        except Exception as e:
            state.failures += 1
            state.last_error = str(e)
            state.last_duration_ms = int((time.perf_counter() - t0) * 1000)

        # Спим до следующего цикла, но реагируем на остановку
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
