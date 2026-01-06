"""
Хелперы для чтения и нормализации переменных окружения.

Зачем нужен модуль:
- централизовать разбор env, чтобы bot/bot_app.py и другие модули не дублировали логику;
- обеспечить единые правила парсинга (пустые строки, thread_id=0);
- упростить повторное использование и тестирование небольших функций.

Как работает:
- читает строки через os.getenv;
- обрезает пробелы, отбрасывает пустые значения;
- преобразует в int при необходимости;
- нормализует thread_id=0 в None (в Telegram 0 невалиден).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EnvDestination:
    """
    Destination, распарсенный из env.

    chat_id обязателен (Telegram chat id, может быть отрицательным).
    thread_id опционален (None означает основной тред чата).
    """
    chat_id: int
    thread_id: Optional[int] = None


def parse_int_env(name: str) -> Optional[int]:
    """
    Читает int из env по имени переменной.

    Возвращает None, если значение отсутствует, пустое или не int.
    """
    raw = os.getenv(name, "")
    # Убираем пробелы и сразу отбрасываем пустые строки.
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def parse_dest_from_env(prefix: str) -> Optional[EnvDestination]:
    """
    Читает destination из env по PREFIX_CHAT_ID / PREFIX_THREAD_ID.

    Правила:
    - chat_id обязателен; если отсутствует -> None.
    - thread_id опционален; если 0 -> считаем None.
    """
    # Парсим chat_id (обязателен).
    chat_id = parse_int_env(f"{prefix}_CHAT_ID")
    if chat_id is None:
        return None

    # Парсим thread_id (опционален) и нормализуем 0 в None.
    thread_id = parse_int_env(f"{prefix}_THREAD_ID")
    if thread_id == 0:
        thread_id = None

    return EnvDestination(chat_id=chat_id, thread_id=thread_id)
