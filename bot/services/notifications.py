"""
Сервис отправки уведомлений и эскалаций.

Содержит:
- основной notify_main с роутингом;
- эскалации (notify_escalation + get_escalations);
- admin alerts при отсутствии destination.
"""

from __future__ import annotations

import logging
import time

from aiogram import Bot

from bot.services.config_sync import ConfigSyncService
from bot.services.observability import ObservabilityService
from bot.utils.notify_router import pick_destinations
from bot.utils.polling import PollingState
from bot.utils.runtime_config import RuntimeConfig


class NotificationService:
    """
    Инкапсулирует всю логику отправки сообщений.
    """

    def __init__(
        self,
        *,
        bot: Bot,
        runtime_config: RuntimeConfig,
        polling_state: PollingState,
        config_sync: ConfigSyncService,
        logger: logging.Logger,
        observability: ObservabilityService,
    ) -> None:
        self._bot = bot
        self._runtime_config = runtime_config
        self._polling_state = polling_state
        self._config_sync = config_sync
        self._logger = logger
        self._observability = observability

    async def notify_main(self, items: list[dict], text: str) -> None:
        """
        Основное уведомление по очереди.
        """
        await self._config_sync.refresh()

        dests = pick_destinations(
            items=items,
            rules=self._runtime_config.routing.rules,
            default_dest=self._runtime_config.routing.default_dest,
            service_id_field=self._runtime_config.routing.service_id_field,
            customer_id_field=self._runtime_config.routing.customer_id_field,
        )
        if not dests:
            await self._observability.handle_no_destination(items)
            return

        for d in dests:
            await self._bot.send_message(chat_id=d.chat_id, message_thread_id=d.thread_id, text=text)

    async def notify_eventlog(self, text: str, items: list[dict]) -> None:
        """
        Уведомления из eventlog (отдельная ветка маршрутизации).
        """
        await self._config_sync.refresh()
        cfg = self._runtime_config.eventlog

        dests = pick_destinations(
            items=items,
            rules=cfg.rules,
            default_dest=cfg.default_dest,
            service_id_field=cfg.service_id_field,
            customer_id_field=cfg.customer_id_field,
        )
        if not dests:
            self._logger.warning("eventlog: no destinations configured")
            return

        for d in dests:
            await self._bot.send_message(chat_id=d.chat_id, message_thread_id=d.thread_id, text=text)

    async def notify_escalation(self, items: list[dict], _marker: str) -> None:
        """
        Эскалации — отдельный поток сообщений.
        """
        await self._config_sync.refresh()
        if not self._runtime_config.escalation.enabled or self._runtime_config.escalation.dest is None:
            return

        text = _build_escalation_text(items, mention=self._runtime_config.escalation.mention)
        d = self._runtime_config.escalation.dest
        await self._bot.send_message(chat_id=d.chat_id, message_thread_id=d.thread_id, text=text)

    def get_escalations(self, items: list[dict]) -> list[dict]:
        """
        Возвращает тикеты, которые должны попасть в эскалацию.
        """
        if not self._runtime_config.escalation.enabled:
            return []
        return self._runtime_config.get_escalations(items)

def _build_escalation_text(items: list[dict], mention: str) -> str:
    # Текст собираем отдельно, чтобы notify_escalation был компактнее.
    now_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
    lines = [
        f"🚨 Эскалация: заявки не взяты в работу вовремя — {now_s}",
        f"{mention} заберите в работу, пожалуйста.",
        "",
    ]
    for it in items:
        lines.append(f"- #{it.get('Id')}: {it.get('Name')}")
    return "\n".join(lines)
