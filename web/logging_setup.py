"""
Настройка логирования web-сервиса.

Используем ключ-значение формат, чтобы логи легко читались и парсились.
"""

from __future__ import annotations

import logging
from typing import Any


class ContextAdapter(logging.LoggerAdapter):
    """
    Добавляет в каждый лог ENVIRONMENT и GIT_SHA.
    """

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        extra = kwargs.get("extra", {})
        extra.setdefault("environment", self.extra.get("environment", "unknown"))
        extra.setdefault("git_sha", self.extra.get("git_sha", "unknown"))
        kwargs["extra"] = extra
        return msg, kwargs


def setup_logging(*, environment: str, git_sha: str) -> ContextAdapter:
    """
    Настраивает логирование в формате key=value.
    """
    logger = logging.getLogger("testci.web")
    if logger.handlers:
        return ContextAdapter(logger, {"environment": environment, "git_sha": git_sha})

    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()

    formatter = logging.Formatter(
        fmt=(
            "ts=%(asctime)s level=%(levelname)s service=web "
            "env=%(environment)s sha=%(git_sha)s "
            "msg=%(message)s"
        )
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False

    return ContextAdapter(logger, {"environment": environment, "git_sha": git_sha})
