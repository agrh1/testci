"""
Сервис отправки уведомлений и эскалаций.

Содержит:
- основной notify_main с роутингом;
- эскалации (notify_escalation + get_escalations);
- admin alerts при отсутствии destination;
- поддержка адаптеров (Telegram + Mattermost).
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from bot.adapters.base import MessageAdapter
from bot.services.config_sync import ConfigSyncService
from bot.services.observability import ObservabilityService
from bot.utils.escalation import EscalationAction
from bot.utils.notify_router import Destination, pick_destinations
from bot.utils.polling import PollingState
from bot.utils.runtime_config import RuntimeConfig


class NotificationService:
    """
    Инкапсулирует всю логику отправки сообщений (Telegram и Mattermost).

    Поддерживает:
    - Telegram (через aiogram Bot и TelegramMessageAdapter)
    - Mattermost (через MattermostMessageAdapter)
    - Dual-mode (отправка в обе платформы одновременно)
    """

    def __init__(
        self,
        *,
        adapters: Optional[Dict[str, MessageAdapter]] = None,  # NEW: platform adapters
        bot: Optional[Bot] = None,  # KEEP for backward compatibility
        runtime_config: RuntimeConfig,
        polling_state: PollingState,
        config_sync: ConfigSyncService,
        logger: logging.Logger,
        observability: ObservabilityService,
    ) -> None:
        self._adapters = adapters or {}
        self._bot = bot  # Keep for backward compatibility (observability)
        self._runtime_config = runtime_config
        self._polling_state = polling_state
        self._config_sync = config_sync
        self._logger = logger
        self._observability = observability

    async def notify_main(self, items: list[dict], text: str) -> None:
        """
        Основное уведомление по очереди.

        ФАЗА 1: использует только Telegram
        ФАЗА 2: отправляет в обе платформы (если DUAL_MODE_ENABLED=1)
        """
        await self._config_sync.refresh()

        dests = pick_destinations(
            items=items,
            rules=self._runtime_config.routing.rules,
            default_dest=self._runtime_config.routing.default_dest,
            service_id_field=self._runtime_config.routing.service_id_field,
            customer_id_field=self._runtime_config.routing.customer_id_field,
            creator_id_field=self._runtime_config.routing.creator_id_field,
            creator_company_id_field=self._runtime_config.routing.creator_company_id_field,
        )
        if not dests:
            await self._observability.handle_no_destination(items)
            return

        for dest in dests:
            await self._send_notification_safe(
                destination=dest,
                text=text,
                context="routing.main",
            )

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
            creator_id_field=cfg.creator_id_field,
            creator_company_id_field=cfg.creator_company_id_field,
        )
        if not dests:
            self._logger.warning("eventlog: no destinations configured")
            return

        for dest in dests:
            await self._send_notification_safe(
                destination=dest,
                text=text,
                context="routing.eventlog",
            )

    async def notify_escalation(self, items: list[EscalationAction], _marker: str) -> None:
        """
        Эскалации — отдельный поток сообщений.
        """
        await self._config_sync.refresh()
        if not self._runtime_config.escalation.enabled:
            return

        for action in items:
            text = _build_escalation_text(action.items, mention=action.mention)
            await self._send_notification_safe(
                destination=action.dest,
                text=text,
                context="routing.escalation",
            )

    def get_escalations(self, items: list[dict]) -> list[EscalationAction]:
        """
        Возвращает тикеты, которые должны попасть в эскалацию.
        """
        if not self._runtime_config.escalation.enabled:
            return []
        return self._runtime_config.get_escalations(items)

    async def _send_notification_safe(
        self,
        *,
        destination: Destination,
        text: str,
        context: str,
    ) -> None:
        """
        Отправить уведомление в destination через подходящий адаптер.

        Поддерживает обе платформы:
        - Telegram: использует chat_id и thread_id
        - Mattermost: использует destination_id и thread_id (root_id)
        """
        try:
            # Получить адаптер для платформы
            adapter = self._adapters.get(destination.platform)
            if not adapter:
                # На ФАЗЕ 1 может быть только Telegram, на ФАЗЕ 2+ обе
                self._logger.warning(
                    "No adapter for platform '%s', skipping notification", destination.platform
                )
                return

            # Отправить через адаптер
            msg_id = await adapter.send_notification(
                destination_id=destination.destination_id or str(destination.chat_id),
                text=text,
                thread_id=str(destination.thread_id) if destination.thread_id else None,
            )

            if msg_id:
                self._logger.debug(
                    "Notification sent to %s/%s: msg_id=%s",
                    destination.platform,
                    destination.destination_id or destination.chat_id,
                    msg_id,
                )
            else:
                self._logger.warning(
                    "Failed to send notification to %s/%s",
                    destination.platform,
                    destination.destination_id or destination.chat_id,
                )

        except TelegramForbiddenError as e:
            # Специфичная обработка Telegram ошибок (для observability)
            self._logger.warning(
                "Forbidden send to %s/%s: %s",
                destination.platform,
                destination.destination_id or destination.chat_id,
                e,
            )
            if destination.platform == "telegram":
                await self._observability.handle_forbidden_send(
                    chat_id=destination.chat_id,
                    thread_id=destination.thread_id,
                    error=str(e),
                    context=context,
                )
        except Exception as e:
            self._logger.error(
                "Error sending notification to %s/%s: %s",
                destination.platform,
                destination.destination_id or destination.chat_id,
                e,
                exc_info=True,
            )

    # ========================================================================
    # DEPRECATED (для backward compatibility) - используйте адаптеры напрямую
    # ========================================================================

    async def _send_message_safe(
        self,
        *,
        chat_id: int,
        thread_id: int | None,
        text: str,
        context: str,
    ) -> None:
        """
        DEPRECATED: используйте _send_notification_safe с Destination вместо этого.

        Оставлен для обратной совместимости с кодом, который еще не обновлен.
        """
        dest = Destination(
            platform="telegram",
            chat_id=chat_id,
            thread_id=thread_id,
        )
        await self._send_notification_safe(
            destination=dest,
            text=text,
            context=context,
        )


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
