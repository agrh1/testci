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

from bot.config.settings import BotSettings
from bot.handlers import commands, errors
from bot.services.config_sync import ConfigSyncService
from bot.services.notifications import NotificationService
from bot.utils.config_client import ConfigClient
from bot.utils.polling import PollingState, polling_open_queue_loop
from bot.utils.runtime_config import RuntimeConfig
from bot.utils.sd_web_client import SdWebClient
from bot.utils.state_store import MemoryStateStore, RedisStateStore, ResilientStateStore, StateStore
from bot.utils.web_client import WebClient
from bot.utils.web_guard import WebGuard


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

    runtime_config = RuntimeConfig(logger=logger, store=state_store, escalation_store_key="bot:escalation")
    config_sync = ConfigSyncService(config_client, runtime_config, logger)

    bot = Bot(token=settings.token)
    dp = Dispatcher()

    # Передаём зависимости в workflow_data, чтобы aiogram смог их инжектить.
    dp.workflow_data["web_client"] = web_client
    dp.workflow_data["web_guard"] = web_guard
    dp.workflow_data["sd_web_client"] = sd_web_client
    dp.workflow_data["config_client"] = config_client
    dp.workflow_data["config_sync"] = config_sync
    dp.workflow_data["polling_state"] = polling_state
    dp.workflow_data["state_store"] = state_store
    dp.workflow_data["runtime_config"] = runtime_config

    dp.errors.register(errors.on_error)
    commands.register_handlers(dp)

    notify_service = NotificationService(
        bot=bot,
        runtime_config=runtime_config,
        polling_state=polling_state,
        config_sync=config_sync,
        logger=logger,
    )

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
        ),
        name="polling_open_queue",
    )

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
        try:
            await polling_task
        except asyncio.CancelledError:
            # Нормально: мы сами отменили фоновую задачу.
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        # Нормально: процесс завершился по сигналу или отмене.
        pass


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
