"""
Главная точка сборки приложения бота.

Здесь мы:
- читаем настройки из env;
- создаём клиентов и сервисы;
- регистрируем хендлеры;
- запускаем polling.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config.settings import BotSettings
from bot.handlers import commands, errors
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
from bot.utils.web_guard import WebGuard


def _build_state_store(settings: BotSettings) -> StateStore:
    """
    Создаёт state store с fallback на память.
    """
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

    # Даже без Redis используем MemoryStateStore, чтобы сохранять состояние в памяти.
    return MemoryStateStore(prefix="testci")


async def main() -> None:
    # Логирование настраиваем до создания клиентов, чтобы ловить все сообщения.
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
    web_guard = WebGuard(web_client)

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
    await user_store.init_from_env(admins=settings.tg_admins, users=settings.tg_users)

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

    bot = Bot(token=settings.token)
    dp = Dispatcher(storage=MemoryStorage())

    # Передаём зависимости в workflow_data, чтобы aiogram смог их инжектить.
    dp.workflow_data["web_client"] = web_client
    dp.workflow_data["web_guard"] = web_guard
    dp.workflow_data["sd_web_client"] = sd_web_client
    dp.workflow_data["config_client"] = config_client
    dp.workflow_data["config_sync"] = config_sync
    dp.workflow_data["polling_state"] = polling_state
    dp.workflow_data["state_store"] = state_store
    dp.workflow_data["runtime_config"] = runtime_config
    dp.workflow_data["user_store"] = user_store
    dp.workflow_data["seafile_store"] = seafile_store
    dp.workflow_data["sd_api_client"] = sd_api_client
    dp.workflow_data["eventlog_filter_store"] = eventlog_filter_store
    dp.workflow_data["service_icon_store"] = service_icon_store
    dp.workflow_data["config_admin_token"] = settings.config_admin_token
    dp.workflow_data["eventlog_login"] = settings.servicedesk_login
    dp.workflow_data["eventlog_password"] = settings.servicedesk_password
    dp.workflow_data["eventlog_base_url"] = settings.eventlog_base_url
    dp.workflow_data["eventlog_start_id"] = settings.eventlog_start_id

    dp.errors.register(errors.on_error)
    commands.register_handlers(dp)

    observability = ObservabilityService(
        bot=bot,
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

    notify_service = NotificationService(
        bot=bot,
        runtime_config=runtime_config,
        polling_state=polling_state,
        config_sync=config_sync,
        logger=logger,
        observability=observability,
    )
    dp.workflow_data["notify_eventlog"] = notify_service.notify_eventlog

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
        """
        Периодические проверки деградации и rollback.
        """
        while not stop_event.is_set():
            await observability.check_web()
            await observability.check_redis()
            await observability.check_rollbacks(
                window_s=settings.obs_rollback_window_s,
                threshold=settings.obs_rollback_threshold,
            )
            await asyncio.sleep(settings.obs_check_interval_s)

    observability_task = asyncio.create_task(observability_loop(), name="observability")

    # Пытаемся сразу подтянуть конфиг при старте (не обязательно, но удобно для диагностики).
    await config_sync.refresh(force=True)

    logger.info(
        "Bot started. WEB_BASE_URL=%s CONFIG_URL=%s CONFIG_VERSION=%s POLL_INTERVAL_S=%s",
        settings.web_base_url,
        settings.config_url,
        runtime_config.version,
        settings.poll_interval_s,
    )

    try:
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        polling_task.cancel()
        observability_task.cancel()
        if eventlog_task is not None:
            eventlog_task.cancel()
        getlink_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            # Нормально: мы сами отменили фоновую задачу.
            pass
        try:
            await observability_task
        except asyncio.CancelledError:
            pass
        if eventlog_task is not None:
            try:
                await eventlog_task
            except asyncio.CancelledError:
                pass
        try:
            await getlink_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Нормально: процесс завершился по сигналу или отмене.
        pass
