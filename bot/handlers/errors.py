"""
Обработчики ошибок для aiogram.
"""

from __future__ import annotations

import logging

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import ErrorEvent


async def on_error(event: ErrorEvent) -> None:
    """
    Логирует непойманные исключения в обработчиках.

    Для известных сетевых ошибок — краткое сообщение (без traceback).
    Для неизвестных — полный traceback для диагностики.
    """
    logger = logging.getLogger("bot.errors")
    exc = event.exception

    # Сетевые ошибки Telegram — частые, не нужен полный traceback
    if isinstance(exc, TelegramNetworkError):
        logger.warning("Telegram network error: %s", exc)
        return

    if isinstance(exc, TelegramRetryAfter):
        logger.warning("Telegram rate limit, retry after %ds", exc.retry_after)
        return

    # Таймауты — тоже краткое сообщение
    if isinstance(exc, (TimeoutError, OSError)):
        logger.warning("Network error in update handler: %s: %s", type(exc).__name__, exc)
        return

    # Всё остальное — полный traceback
    logger.error("Unhandled error: %s: %s", type(exc).__name__, exc, exc_info=True)
