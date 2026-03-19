"""
Observability для бота.

Содержит:
- алерт "нет destination" (27A);
- алерт по деградации web/redis (27D);
- алерт при частых rollback (27B).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from bot.adapters.base import MessageAdapter
from bot.utils.admin_alerts import (
    AdminAlertDestination,
    build_forbidden_send_alert_text,
    build_no_destination_alert_text,
    build_redis_degraded_alert_text,
    build_rollbacks_alert_text,
    build_web_degraded_alert_text,
    parse_admin_alert_dest_from_env,
)
from bot.utils.polling import PollingState
from bot.utils.runtime_config import RuntimeConfig
from bot.utils.state_store import StateStore
from bot.utils.web_client import WebClient


class ObservabilityService:
    """
    Инкапсулирует admin-алерты и проверки деградации.
    Отправляет алерты через Mattermost adapter.
    """

    def __init__(
        self,
        *,
        mm_adapter: MessageAdapter,
        polling_state: PollingState,
        runtime_config: RuntimeConfig,
        web_client: WebClient,
        state_store: Optional[StateStore],
        logger: logging.Logger,
        config_admin_token: str,
        admin_alert_min_interval_s: float,
        web_alert_min_interval_s: float,
        redis_alert_min_interval_s: float,
        rollback_alert_min_interval_s: float,
    ) -> None:
        self._mm_adapter = mm_adapter
        self._polling_state = polling_state
        self._runtime_config = runtime_config
        self._web_client = web_client
        self._state_store = state_store
        self._logger = logger
        self._config_admin_token = config_admin_token
        self._admin_alert_min_interval_s = admin_alert_min_interval_s
        self._web_alert_min_interval_s = web_alert_min_interval_s
        self._redis_alert_min_interval_s = redis_alert_min_interval_s
        self._rollback_alert_min_interval_s = rollback_alert_min_interval_s

    async def _send_alert(self, dest: "AdminAlertDestination", text: str) -> None:
        """Отправить алерт через MM adapter."""
        try:
            await self._mm_adapter.send_notification(
                destination_id=dest.channel_id,
                text=text,
                thread_id=dest.thread_id,
            )
        except Exception as e:
            self._logger.exception("Failed to send alert: %s", e)

    async def handle_no_destination(self, items: list[dict]) -> None:
        logger = logging.getLogger("bot.routing_observability")

        now = time.time()
        self._polling_state.tickets_without_destination_total += 1
        self._polling_state.last_ticket_without_destination_at = now

        min_interval_s = self._admin_alert_min_interval_s
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
                "No destinations and ADMIN_ALERT_CHANNEL_ID not set; cannot send admin alert."
            )
            return

        await self._send_alert(dest_admin, alert_text)

    async def handle_forbidden_send(
        self,
        *,
        channel_id: str,
        thread_id: Optional[str],
        error: str,
        context: Optional[str] = None,
    ) -> None:
        logger = logging.getLogger("bot.routing_observability")
        dest_admin = parse_admin_alert_dest_from_env()
        if dest_admin is None:
            logger.warning(
                "Forbidden send to channel_id=%s; ADMIN_ALERT_CHANNEL_ID not set.",
                channel_id,
            )
            return

        alert_text = build_forbidden_send_alert_text(
            channel_id=channel_id,
            thread_id=thread_id,
            error=error,
            context=context,
        )

        await self._send_alert(dest_admin, alert_text)

    async def check_web(self) -> None:
        attempts = 3
        last_health = None
        last_ready = None
        for i in range(attempts):
            health, ready = await self._web_client.check_health_ready(force=True)
            last_health = health
            last_ready = ready
            if health.ok and ready.ok:
                return
            if i < attempts - 1:
                await asyncio.sleep(0.5)

        if last_health is None or last_ready is None:
            return

        now = time.time()
        min_interval_s = self._web_alert_min_interval_s
        if (
            self._polling_state.last_web_alert_at is not None
            and (now - float(self._polling_state.last_web_alert_at)) < min_interval_s
        ):
            self._polling_state.web_alerts_skipped_rate_limit += 1
            return

        dest_admin = parse_admin_alert_dest_from_env()
        if dest_admin is None:
            self._logger.warning("WEB degraded but no admin destination configured.")
            return

        text = build_web_degraded_alert_text(
            health_ok=last_health.ok,
            ready_ok=last_ready.ok,
            health_status=last_health.status,
            ready_status=last_ready.status,
            health_error=last_health.error,
            ready_error=last_ready.error,
            attempts=attempts,
        )

        self._polling_state.last_web_alert_at = now
        await self._send_alert(dest_admin, text)

    async def check_redis(self) -> None:
        if self._state_store is None:
            return

        ping_fn = getattr(self._state_store, "ping", None)
        if not callable(ping_fn):
            return

        error = ""
        try:
            ping_fn()
        except Exception as e:
            error = str(e)

        if not error:
            return

        now = time.time()
        min_interval_s = self._redis_alert_min_interval_s
        if (
            self._polling_state.last_redis_alert_at is not None
            and (now - float(self._polling_state.last_redis_alert_at)) < min_interval_s
        ):
            self._polling_state.redis_alerts_skipped_rate_limit += 1
            return

        dest_admin = parse_admin_alert_dest_from_env()
        if dest_admin is None:
            self._logger.warning("Redis degraded but no admin destination configured.")
            return

        last_ok_ts = getattr(self._state_store, "last_ok_ts", None)
        text = build_redis_degraded_alert_text(error=error, last_ok_ts=last_ok_ts)

        self._polling_state.last_redis_alert_at = now
        await self._send_alert(dest_admin, text)

    async def check_rollbacks(self, *, window_s: int, threshold: int) -> None:
        if not self._config_admin_token:
            return

        res = await self._web_client.get_rollbacks(window_s=window_s, admin_token=self._config_admin_token)
        if not res.get("ok"):
            return

        data = res.get("data", {})
        count = int(data.get("count", 0))
        last_at = data.get("last_rollback_at")
        window_s = int(data.get("window_s", window_s))

        if count < threshold:
            return

        now = time.time()
        min_interval_s = self._rollback_alert_min_interval_s
        if (
            self._polling_state.last_rollback_alert_at is not None
            and (now - float(self._polling_state.last_rollback_alert_at)) < min_interval_s
        ):
            self._polling_state.rollback_alerts_skipped_rate_limit += 1
            return

        dest_admin = parse_admin_alert_dest_from_env()
        if dest_admin is None:
            self._logger.warning("Rollback alert but no admin destination configured.")
            return

        text = build_rollbacks_alert_text(count=count, window_s=window_s, last_at=last_at)

        self._polling_state.last_rollback_alert_at = now
        await self._send_alert(dest_admin, text)


