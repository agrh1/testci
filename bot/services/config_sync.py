"""
Сервис синхронизации runtime-конфига с web /config.

Задача:
- получить конфиг через ConfigClient;
- применить его через RuntimeConfig;
- аккуратно залогировать ошибки и обновления.
"""

from __future__ import annotations

import logging

from bot.utils.config_client import ConfigClient
from bot.utils.runtime_config import RuntimeConfig


class ConfigSyncService:
    """
    Инкапсулирует обновление runtime-конфига, чтобы переиспользовать в хендлерах и polling.
    """

    def __init__(self, config_client: ConfigClient, runtime_config: RuntimeConfig, logger: logging.Logger) -> None:
        self._config_client = config_client
        self._runtime_config = runtime_config
        self._logger = logger

    async def refresh(self, force: bool = False) -> None:
        """
        Подтягивает /config и, если версия выросла, применяет.

        Важно:
        - при ошибке fetch не падаем (ConfigClient сам вернёт cached, если он есть);
        - RuntimeConfig валидирует и применяет только корректные обновления.
        """
        res = await self._config_client.get(force=force)
        if not res.ok or res.data is None:
            if res.error:
                self._logger.warning("config fetch failed: %s", res.error)
            return

        updated = self._runtime_config.apply_from_web_config(res.data)
        if updated:
            self._logger.info(
                "config updated: version=%s source=%s",
                self._runtime_config.version,
                self._runtime_config.source,
            )
