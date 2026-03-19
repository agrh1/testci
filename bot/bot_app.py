"""
Главная точка сборки приложения бота (Mattermost-only, v3).
# rebuild: 2026-03-19.2 — fix split(maxsplit=None) TypeError, fix StateManager methods

Здесь мы:
- читаем настройки из env;
- создаём клиентов и сервисы;
- запускаем Mattermost WebSocket бот;
- ожидаем stop_event для graceful shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import aiohttp

from bot.adapters.base import UserIdentity
from bot.adapters.mattermost import MattermostMessageAdapter, MattermostStateManager
from bot.adapters.mattermost_bot import MattermostBotAdapter
from bot.config.settings import BotSettings
from bot.services.command_executor import CommandExecutor, CommandRequest
from bot.services.config_sync import ConfigSyncService
from bot.services.eventlog_filter_store import EventlogFilterStore
from bot.services.eventlog_worker import eventlog_loop
from bot.services.getlink_worker import getlink_poll_loop
from bot.services.notifications import NotificationService
from bot.services.observability import ObservabilityService
from bot.services.seafile_store import SeafileServiceStore
from bot.services.service_icon_store import ServiceIconStore
from bot.services.user_store import UserStore
from bot.utils.config_client import ConfigClient
from bot.utils.polling import PollingState, polling_open_queue_loop
from bot.utils.runtime_config import RuntimeConfig
from bot.utils.sd_api_client import SdApiClient, SdApiConfig
from bot.utils.sd_web_client import SdWebClient
from bot.utils.state_store import MemoryStateStore, RedisStateStore, ResilientStateStore, StateStore
from bot.utils.web_client import WebClient


def _build_state_store(settings: BotSettings) -> StateStore:
    if settings.redis_url:
        primary = RedisStateStore(
            settings.redis_url,
            prefix="testci",
            socket_timeout_s=settings.redis_socket_timeout_s,
            socket_connect_timeout_s=settings.redis_connect_timeout_s,
        )
        fallback = MemoryStateStore(prefix="testci")
        state_store = ResilientStateStore(primary, fallback)
        with contextlib.suppress(Exception):
            getattr(state_store, "ping", lambda: None)()
        return state_store

    return MemoryStateStore(prefix="testci")


async def main() -> None:
    settings = BotSettings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("bot")

    web_client = WebClient(
        base_url=settings.web_base_url,
        timeout_s=settings.web_timeout_s,
        cache_ttl_s=settings.web_cache_ttl_s,
    )
    sd_web_client = SdWebClient(
        base_url=settings.web_base_url,
        timeout_s=settings.sd_web_timeout_s,
    )

    config_client = ConfigClient(
        url=settings.config_url,
        token=settings.config_token,
        timeout_s=settings.config_timeout_s,
        cache_ttl_s=settings.config_ttl_s,
    )

    state_store = _build_state_store(settings)

    polling_state = PollingState()
    stop_event = asyncio.Event()

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for bot user storage")

    user_store = UserStore(settings.database_url)
    await user_store.init_schema()

    seafile_store = SeafileServiceStore(settings.database_url)
    await seafile_store.init_schema()

    eventlog_filter_store = EventlogFilterStore(settings.database_url)
    await eventlog_filter_store.init_schema()

    service_icon_store = ServiceIconStore(settings.database_url)
    await service_icon_store.init_schema()

    sd_api_client = SdApiClient(
        SdApiConfig(
            base_url=settings.servicedesk_base_url,
            login=settings.servicedesk_login,
            password=settings.servicedesk_password,
            timeout_s=settings.servicedesk_timeout_s,
        )
    )

    runtime_config = RuntimeConfig(logger=logger, store=state_store, escalation_store_key="bot:escalation")
    config_sync = ConfigSyncService(config_client, runtime_config, logger)

    # ========================================================================
    # Mattermost adapter
    # ========================================================================

    mattermost_session = aiohttp.ClientSession()
    mattermost_adapter = MattermostMessageAdapter(
        api_url=settings.mattermost_api_url,
        bot_token=settings.mattermost_bot_token,
        session=mattermost_session,
        logger=logger,
    )

    adapters = {"mattermost": mattermost_adapter}
    state_managers = {"mattermost": MattermostStateManager(store=state_store, logger=logger)}

    logger.info("Mattermost adapter initialized: %s", settings.mattermost_api_url)

    # ========================================================================
    # Command executor
    # ========================================================================

    command_executor = CommandExecutor(
        adapters=adapters,
        state_managers=state_managers,
        db=user_store,
        logger=logger,
    )

    # Import command implementations
    from bot.handlers.command_implementations import (
        cmd_admin_add,
        cmd_config,
        cmd_config_diff,
        cmd_escalation_send_test,
        cmd_eventlog_poll,
        cmd_help_mattermost,
        cmd_last_eventlog_id,
        cmd_routes_debug,
        cmd_routes_send_test,
        cmd_routes_test,
        cmd_service_icon_add,
        cmd_service_icons,
        cmd_user_add,
        cmd_user_audit,
        cmd_user_history,
        cmd_user_list,
        cmd_user_remove,
        cmd_whereami,
        cmd_whoami,
    )

    command_executor.set_dependencies({
        "config_sync": config_sync,
        "runtime_config": runtime_config,
        "notification_service": None,  # Will be set after NotificationService is created
        "user_store": user_store,
        "web_client": web_client,
        "config_admin_token": settings.config_admin_token,
        "state_store": state_store,
        "eventlog_filter_store": eventlog_filter_store,
        "eventlog_login": settings.servicedesk_login,
        "eventlog_password": settings.servicedesk_password,
        "eventlog_base_url": settings.eventlog_base_url,
        "eventlog_start_id": settings.eventlog_start_id,
        "service_icon_store": service_icon_store,
    })

    # Register commands
    command_executor.register_handler("routes_test", cmd_routes_test)
    command_executor.register_handler("routes_debug", cmd_routes_debug)
    command_executor.register_handler("routes_send_test", cmd_routes_send_test)
    command_executor.register_handler("escalation_send_test", cmd_escalation_send_test)
    command_executor.register_handler("user_add", cmd_user_add)
    command_executor.register_handler("user_remove", cmd_user_remove)
    command_executor.register_handler("admin_add", cmd_admin_add)
    command_executor.register_handler("user_list", cmd_user_list)
    command_executor.register_handler("user_history", cmd_user_history)
    command_executor.register_handler("user_audit", cmd_user_audit)
    command_executor.register_handler("config", cmd_config)
    command_executor.register_handler("config_diff", cmd_config_diff)
    command_executor.register_handler("last_eventlog_id", cmd_last_eventlog_id)
    command_executor.register_handler("eventlog_poll", cmd_eventlog_poll)
    command_executor.register_handler("service_icons", cmd_service_icons)
    command_executor.register_handler("service_icon_add", cmd_service_icon_add)
    command_executor.register_handler("help_mattermost", cmd_help_mattermost)
    command_executor.register_handler("whoami", cmd_whoami)
    command_executor.register_handler("whereami", cmd_whereami)

    # ========================================================================
    # Observability service
    # ========================================================================

    observability = ObservabilityService(
        mm_adapter=mattermost_adapter,
        polling_state=polling_state,
        runtime_config=runtime_config,
        web_client=web_client,
        state_store=state_store,
        logger=logger,
        config_admin_token=settings.config_admin_token,
        admin_alert_min_interval_s=settings.admin_alert_min_interval_s,
        web_alert_min_interval_s=settings.obs_web_alert_min_interval_s,
        redis_alert_min_interval_s=settings.obs_redis_alert_min_interval_s,
        rollback_alert_min_interval_s=settings.obs_rollback_alert_min_interval_s,
    )

    # ========================================================================
    # Notification service
    # ========================================================================

    notify_service = NotificationService(
        adapters=adapters,
        runtime_config=runtime_config,
        polling_state=polling_state,
        config_sync=config_sync,
        logger=logger,
        observability=observability,
    )

    command_executor.set_dependency("notification_service", notify_service)

    # ========================================================================
    # Mattermost Bot (WebSocket)
    # ========================================================================

    async def handle_mattermost_command(
        command: str,
        text: str,
        user_id: str,
        channel_id: str,
        post_id: str,
    ) -> None:
        try:
            user_info = await mattermost_bot.get_user_info(user_id)
            if not user_info:
                logger.warning("Could not get user info for %s", user_id)
                return

            role = await user_store.get_role_by_mm_id(user_id)
            if role is None:
                await mattermost_bot.send_notification(
                    destination_id=channel_id,
                    text="⛔ Вы не зарегистрированы. Обратитесь к администратору.",
                    thread_id=post_id,
                )
                return

            request = CommandRequest(
                user=UserIdentity(
                    user_id=user_id,
                    platform="mattermost",
                    username=user_info.get("username", ""),
                ),
                command=command,
                raw_text=text,
                is_admin=(role == "admin"),
                context={
                    "channel_id": channel_id,
                    "post_id": post_id,
                    "user_info": user_info,
                },
            )

            response = await command_executor.execute(request)

            if response and response.text:
                await mattermost_bot.send_notification(
                    destination_id=channel_id,
                    text=response.text,
                    thread_id=post_id,
                )

        except Exception as e:
            logger.error("MM command error (%s): %s: %s", command, type(e).__name__, e, exc_info=True)
            with contextlib.suppress(Exception):
                await mattermost_bot.send_notification(
                    destination_id=channel_id,
                    text=(
                        f"❌ Внутренняя ошибка при обработке команды /{command}\n"
                        f"Тип: {type(e).__name__}\n"
                        f"Описание: {str(e) or 'нет описания'}\n"
                        f"Обратитесь к администратору."
                    ),
                    thread_id=post_id,
                )

    mattermost_bot = MattermostBotAdapter(
        server_url=settings.mattermost_api_url.removesuffix('/api/v4').rstrip('/'),
        bot_token=settings.mattermost_bot_token,
        logger=logger,
        on_command=handle_mattermost_command,
    )
    command_executor.set_dependency("mattermost_bot", mattermost_bot)

    async def run_mattermost_bot() -> None:
        try:
            await mattermost_bot.start()
            await asyncio.sleep(2)

            startup_message = (
                "🟢 **I'm alive!**\n\n"
                "ServiceBot запущен и слушает команды.\n\n"
                "Введите `help_mattermost` чтобы увидеть список всех команд."
            )

            with contextlib.suppress(Exception):
                await mattermost_bot.send_notification(
                    destination_id=settings.mattermost_startup_channel_id or "town-square",
                    text=startup_message,
                )

            logger.info("✓ Mattermost Bot is running")

        except Exception as e:
            logger.error("Mattermost bot error: %s: %s", type(e).__name__, e)

    mattermost_bot_task = asyncio.create_task(run_mattermost_bot(), name="mattermost_bot")

    # ========================================================================
    # Background tasks
    # ========================================================================

    polling_task = asyncio.create_task(
        polling_open_queue_loop(
            state=polling_state,
            stop_event=stop_event,
            sd_web_client=sd_web_client,
            notify_main=notify_service.notify_main,
            notify_escalation=notify_service.notify_escalation,
            get_escalations=notify_service.get_escalations,
            refresh_config=config_sync.refresh,
            base_interval_s=settings.poll_interval_s,
            max_backoff_s=settings.poll_max_backoff_s,
            min_notify_interval_s=settings.min_notify_interval_s,
            max_items_in_message=settings.max_items_in_message,
            store=state_store,
            store_key="bot:open_queue",
            service_icon_store=service_icon_store,
        ),
        name="polling_open_queue",
    )

    eventlog_task = None
    if settings.eventlog_enabled:
        eventlog_task = asyncio.create_task(
            eventlog_loop(
                stop_event=stop_event,
                notify_eventlog=notify_service.notify_eventlog,
                store=state_store,
                filter_store=eventlog_filter_store,
                login=settings.servicedesk_login,
                password=settings.servicedesk_password,
                base_url=settings.eventlog_base_url,
                poll_interval_s=settings.eventlog_poll_interval_s,
                keepalive_every=settings.eventlog_keepalive_every,
                start_event_id=settings.eventlog_start_id,
            ),
            name="eventlog_loop",
        )

    getlink_task = asyncio.create_task(
        getlink_poll_loop(
            sd_api_client=sd_api_client,
            seafile_store=seafile_store,
            interval_s=settings.getlink_poll_interval_s,
            lookback_s=settings.getlink_lookback_s,
            stop_event=stop_event,
        ),
        name="getlink_poll",
    )

    async def observability_loop() -> None:
        while not stop_event.is_set():
            await observability.check_web()
            await observability.check_redis()
            await observability.check_rollbacks(
                window_s=settings.obs_rollback_window_s,
                threshold=settings.obs_rollback_threshold,
            )
            await asyncio.sleep(settings.obs_check_interval_s)

    observability_task = asyncio.create_task(observability_loop(), name="observability")

    await config_sync.refresh(force=True)

    logger.info(
        "Bot started (MM-only). MATTERMOST_API_URL=%s CONFIG_URL=%s CONFIG_VERSION=%s POLL_INTERVAL_S=%s",
        settings.mattermost_api_url,
        settings.config_url,
        runtime_config.version,
        settings.poll_interval_s,
    )

    # ========================================================================
    # Main loop: wait for stop signal
    # ========================================================================

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down...")
        stop_event.set()
        polling_task.cancel()
        observability_task.cancel()
        if eventlog_task is not None:
            eventlog_task.cancel()
        getlink_task.cancel()
        mattermost_bot_task.cancel()

        for task in [polling_task, observability_task, eventlog_task, getlink_task, mattermost_bot_task]:
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        await mattermost_bot.stop()
        await mattermost_session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
