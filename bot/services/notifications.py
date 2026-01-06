"""
Сервис отправки уведомлений и эскалаций.

Содержит:
- основной notify_main с роутингом;
- эскалации (notify_escalation + get_escalations);
- admin alerts при отсутствии destination.
"""

from __future__ import annotations

import logging
import os
import time

from aiogram import Bot

from bot.services.config_sync import ConfigSyncService
from bot.utils.admin_alerts import build_no_destination_alert_text, parse_admin_alert_dest_from_env
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
    ) -> None:
        self._bot = bot
        self._runtime_config = runtime_config
        self._polling_state = polling_state
        self._config_sync = config_sync
        self._logger = logger

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
            await self._handle_no_destination(items)
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

    async def _handle_no_destination(self, items: list[dict]) -> None:
        """
        Шаг 27A: тикет пришёл, но destinations не найден.
        """
        logger = logging.getLogger("bot.routing_observability")

        now = time.time()
        self._polling_state.tickets_without_destination_total += 1
        self._polling_state.last_ticket_without_destination_at = now

        min_interval_s = float(os.getenv("ADMIN_ALERT_MIN_INTERVAL_S", "300"))
        if (
            self._polling_state.last_admin_alert_at is not None
            and (now - float(self._polling_state.last_admin_alert_at)) < min_interval_s
        ):
            self._polling_state.admin_alerts_skipped_rate_limit += 1
            logger.info("No destinations; admin alert skipped by rate-limit.")
            return

        dest_admin = parse_admin_alert_dest_from_env()
        alert_text = build_no_destination_alert_text(
            ticket=items[0] if items else None,
            rules_count=len(self._runtime_config.routing.rules),
            default_dest_present=self._runtime_config.routing.default_dest is not None,
            service_id_field=self._runtime_config.routing.service_id_field,
            customer_id_field=self._runtime_config.routing.customer_id_field,
            config_version=self._runtime_config.version,
            config_source=self._runtime_config.source,
        )

        self._polling_state.last_admin_alert_at = now

        if dest_admin is None:
            logger.warning(
                "No destinations and ADMIN_ALERT_CHAT_ID/ALERT_CHAT_ID not set; cannot send admin alert."
            )
            return

        try:
            await self._bot.send_message(
                chat_id=dest_admin.chat_id,
                message_thread_id=dest_admin.thread_id,
                text=alert_text,
            )
        except Exception as e:
            logger.exception("Failed to send admin alert: %s", e)


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
