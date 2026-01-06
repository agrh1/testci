"""
Обработчики ошибок для aiogram.
"""

from __future__ import annotations

import logging

from aiogram.types import ErrorEvent


async def on_error(event: ErrorEvent) -> None:
    """
    Логирует непойманные исключения в обработчиках.
    """
    logger = logging.getLogger("bot.errors")
    logger.exception("Unhandled exception in update handling: %s", event.exception)
