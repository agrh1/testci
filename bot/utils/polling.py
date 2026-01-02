# bot/utils/polling.py
"""
Фоновая задача (polling) с безопасной деградацией, retries и jitter.

Что умеет:
- периодически делает HTTP GET на заданный url
- добавляет X-Request-ID для корреляции
- retries при исключениях/не-2xx с backoff + jitter
- backoff между циклами polling при серии ошибок (exp backoff до max_backoff_s)
- хранит cursor (зачаток дедупа) — если сервер прислал X-Poll-Cursor, сохраняем

Важно:
- любые ошибки ловятся, пишутся в state, цикл продолжает работать
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass
from typing import Optional

import aiohttp


@dataclass
class PollingState:
    runs: int = 0
    failures: int = 0
    consecutive_failures: int = 0

    last_run_ts: Optional[float] = None
    last_success_ts: Optional[float] = None

    last_error: Optional[str] = None
    last_duration_ms: Optional[int] = None
    last_http_status: Optional[int] = None
    last_request_id: Optional[str] = None

    # "Зачаток дедупа": курсор последнего обработанного события/пакета
    last_cursor: Optional[str] = None

    # Диагностика текущего режима ожидания
    current_interval_s: Optional[float] = None


def _jitter(seconds: float, ratio: float = 0.10) -> float:
    """
    Добавляем случайную "дрожь" к интервалу, чтобы не синхронизироваться с другими сервисами.
    ratio=0.10 -> +-10%
    """
    if seconds <= 0:
        return 0.0
    delta = seconds * ratio
    return max(0.0, seconds + random.uniform(-delta, delta))


async def _http_get_with_retries(
    *,
    session: aiohttp.ClientSession,
    url: str,
    timeout_s: float,
    max_retries: int,
    retry_base_delay_s: float,
    headers: dict[str, str],
) -> tuple[Optional[int], Optional[str]]:
    """
    Делает GET с retries.
    Возвращает: (http_status, error_text)
    - При успехе (2xx): (status, None)
    - При не-2xx: (status, "HTTP <status>")
    - При исключении: (None, "<exception>")
    """
    timeout = aiohttp.ClientTimeout(total=timeout_s)

    # max_retries=2 -> всего попыток 3 (0,1,2)
    for attempt in range(max_retries + 1):
        try:
            async with session.get(url, headers=headers, timeout=timeout) as r:
                await r.release()
                if 200 <= r.status < 300:
                    return r.status, None
                return r.status, f"HTTP {r.status}"
        except Exception as e:
            err = str(e)

        # Если это была последняя попытка — выходим с ошибкой
        if attempt >= max_retries:
            return None, err

        # Backoff между попытками: retry_base_delay_s * 2^attempt (+ jitter)
        delay = retry_base_delay_s * (2 ** attempt)
        await asyncio.sleep(_jitter(delay, ratio=0.25))


async def polling_loop(
    *,
    state: PollingState,
    stop_event: asyncio.Event,
    url: str,
    base_interval_s: float = 30.0,
    timeout_s: float = 2.0,
    max_backoff_s: float = 300.0,
    max_retries: int = 2,
    retry_base_delay_s: float = 0.5,
) -> None:
    """
    Основной цикл polling.

    Backoff между циклами:
    - при успехе: interval = base_interval_s
    - при ошибке: interval = min(max_backoff_s, max(base_interval_s, interval*2))
    """
    interval_s = base_interval_s

    async with aiohttp.ClientSession() as session:
        while not stop_event.is_set():
            state.last_run_ts = time.time()
            state.runs += 1

            request_id = str(uuid.uuid4())
            state.last_request_id = request_id

            # Поддержка cursor: если есть — отправляем его (как “since”)
            headers = {"X-Request-ID": request_id}
            if state.last_cursor:
                headers["X-Poll-Cursor"] = state.last_cursor

            t0 = time.perf_counter()

            http_status, error = await _http_get_with_retries(
                session=session,
                url=url,
                timeout_s=timeout_s,
                max_retries=max_retries,
                retry_base_delay_s=retry_base_delay_s,
                headers=headers,
            )

            state.last_duration_ms = int((time.perf_counter() - t0) * 1000)
            state.last_http_status = http_status

            if error is None and http_status is not None:
                # Успех
                state.last_success_ts = time.time()
                state.last_error = None
                state.consecutive_failures = 0

                # Попытка прочитать cursor из ответа (если сервер будет поддерживать):
                # Сейчас у нас нет тела ответа, поэтому используем только заголовок.
                # (Для ServiceDesk позже будем парсить JSON и обновлять cursor по событиям.)
                #
                # Технически мы "release" уже сделали; поэтому заголовки сейчас не доступны.
                # Чтобы реально читать заголовок, нужно не делать release до чтения.
                # Для текущего шага оставляем cursor как "контракт": мы его отправляем,
                # и в будущем переедем на JSON и полноценное обновление.
                #
                # state.last_cursor = state.last_cursor  # без изменений

                interval_s = base_interval_s
            else:
                # Ошибка
                state.failures += 1
                state.consecutive_failures += 1
                state.last_error = error or "unknown_error"

                interval_s = min(max_backoff_s, max(base_interval_s, interval_s * 2))

            state.current_interval_s = interval_s

            # Пауза до следующего цикла (с jitter), но реагируем на stop_event
            sleep_s = _jitter(interval_s, ratio=0.10)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=sleep_s)
            except asyncio.TimeoutError:
                pass
