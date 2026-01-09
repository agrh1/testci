"""
Командные обработчики бота.

Содержит пользовательские и админские команды.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import ping_reply_text
from bot.config.settings import get_env
from bot.middlewares.access_control import AccessControlMiddleware, AccessPolicy
from bot.services.config_sync import ConfigSyncService
from bot.services.eventlog_worker import EVENTLOG_STATE_KEY, eventlog_poll_once
from bot.services.seafile_store import SeafileServiceStore
from bot.services.user_store import TgProfile, UserStore
from bot.utils.env_helpers import get_version_info
from bot.utils.escalation import EscalationFilter
from bot.utils.notify_router import explain_matches, pick_destinations
from bot.utils.polling import PollingState
from bot.utils.runtime_config import RuntimeConfig
from bot.utils.sd_api_client import SdApiClient
from bot.utils.sd_web_client import SdWebClient
from bot.utils.seafile_client import getlink
from bot.utils.state_store import StateStore
from bot.utils.web_client import WebClient
from bot.utils.web_filters import WebReadyFilter

_PENDING_SHARE_CONTACT: dict[int, dict[str, object]] = {}
_PENDING_RESET_PASSWORD: dict[int, dict[str, object]] = {}


class LinkRequest(StatesGroup):
    waiting_for_service = State()
    waiting_for_ticket = State()


def register_handlers(dp: Dispatcher) -> None:
    """
    Регистрирует все командные хендлеры в Dispatcher.
    """
    admin_router = Router()
    user_router = Router()

    # Middleware доступа: admin — только админские команды, user — user+admin.
    admin_router.message.middleware(AccessControlMiddleware(policy=AccessPolicy(required_role="admin")))
    user_router.message.middleware(AccessControlMiddleware(policy=AccessPolicy(required_role="user")))

    user_router.message.register(cmd_start, Command("start"))
    user_router.message.register(cmd_help, Command("help"))
    user_router.message.register(cmd_ping, Command("ping"))
    user_router.message.register(cmd_share_phone, Command("share_phone"))
    user_router.message.register(cmd_save_contact, F.contact)
    user_router.message.register(cmd_reset_password, Command("reset_password"))
    user_router.message.register(cmd_get_link, Command("get_link"))
    user_router.message.register(cmd_get_link_ticket, StateFilter(LinkRequest.waiting_for_ticket))

    admin_router.message.register(cmd_status, Command("status"))
    admin_router.message.register(cmd_needs_web, Command("needs_web"), WebReadyFilter("/needs_web"))

    user_router.message.register(cmd_sd_open, Command("sd_open"))

    admin_router.message.register(cmd_routes_test, Command("routes_test"))
    admin_router.message.register(cmd_routes_debug, Command("routes_debug"))
    admin_router.message.register(cmd_routes_send_test, Command("routes_send_test"))
    admin_router.message.register(cmd_escalation_send_test, Command("escalation_send_test"))
    admin_router.message.register(cmd_user_add, Command("user_add"))
    admin_router.message.register(cmd_user_remove, Command("user_remove"))
    admin_router.message.register(cmd_admin_add, Command("admin_add"))
    admin_router.message.register(cmd_user_list, Command("user_list"))
    admin_router.message.register(cmd_help_admin, Command("help_admin"))
    admin_router.message.register(cmd_user_history, Command("user_history"))
    admin_router.message.register(cmd_user_audit, Command("user_audit"))
    admin_router.message.register(cmd_share_contact, Command("share_contact"))
    admin_router.message.register(cmd_share_contact_phone, _is_pending_share_contact)
    admin_router.message.register(cmd_config_diff, Command("config_diff"))
    admin_router.message.register(cmd_last_eventlog_id, Command("last_eventlog_id"))
    admin_router.message.register(cmd_eventlog_poll, Command("eventlog_poll"))

    user_router.callback_query.register(cb_reset_password_cancel, F.data == "rp:cancel")
    user_router.callback_query.register(
        cb_reset_password,
        F.data.startswith("rp:") & (F.data != "rp:cancel"),
    )
    user_router.callback_query.register(
        cb_get_link_service,
        F.data.startswith("gl:"),
        StateFilter(LinkRequest.waiting_for_service),
    )

    dp.include_router(user_router)
    dp.include_router(admin_router)


def _fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _format_check_line(
    title: str,
    ok: bool,
    status: Optional[int],
    duration_ms: int,
    request_id: str,
    error: Optional[str],
) -> str:
    icon = "✅" if ok else "❌"
    status_s = str(status) if status is not None else "—"
    err = f", err={error}" if error else ""
    return f"{icon} {title}: status={status_s}, {duration_ms}ms, request_id={request_id}{err}"


def _to_int(x: str) -> Optional[int]:
    try:
        x = x.strip()
        if not x:
            return None
        return int(x)
    except Exception:
        return None


def _parse_kv_args(text: str) -> dict[str, str]:
    parts = text.split()
    out: dict[str, str] = {}
    for p in parts[1:]:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        out[k.strip().lower()] = v.strip()

    # Поддержка name="..." с пробелами внутри.
    if 'name="' in text:
        start = text.find('name="')
        if start != -1:
            start += len('name="')
            end = text.find('"', start)
            if end != -1:
                out["name"] = text[start:end]
    return out


def _build_fake_item(
    *,
    name: str,
    service_id_field: str,
    customer_id_field: str,
    service_id: Optional[int],
    customer_id: Optional[int],
) -> dict:
    # Минимальный объект тикета для тестовых команд.
    it = {"Id": 999999, "Name": name}
    if service_id is not None:
        it[service_id_field] = service_id
    if customer_id is not None:
        it[customer_id_field] = customer_id
    return it


def _match_escalation_filter(item: dict, flt: EscalationFilter, service_id_field: str, customer_id_field: str) -> bool:
    """
    Простой матч фильтра эскалации для тестовой команды.
    Логика совпадает с EscalationManager:
    - если фильтр пустой -> True
    - иначе: keyword OR service_id OR customer_id
    """
    if not flt.keywords and not flt.service_ids and not flt.customer_ids:
        return True

    name = item.get("Name")
    if flt.keywords and isinstance(name, str):
        n = name.strip().lower()
        if any(k in n for k in flt.keywords):
            return True

    if flt.service_ids:
        try:
            sid = int(item.get(service_id_field))
            if sid in flt.service_ids:
                return True
        except Exception:
            pass

    if flt.customer_ids:
        try:
            cid = int(item.get(customer_id_field))
            if cid in flt.customer_ids:
                return True
        except Exception:
            pass

    return False


async def cmd_start(message: Message, user_store: UserStore) -> None:
    role = None
    if message.from_user is not None:
        role = await user_store.get_role(message.from_user.id)

    text = (
        "Доступные команды:\n"
        "- /ping\n"
        "- /help\n"
        "- /share_phone (передать телефон для профиля)\n"
        "- /sd_open — показать открытые заявки\n"
        "- /reset_password — сбросить пароль в SD\n"
        "- /get_link — ссылка на загрузку логов"
    )
    if role == "admin":
        text += "\n\nАдминские команды:\n- /help_admin"

    await message.answer(text)


async def cmd_ping(message: Message) -> None:
    await message.answer(ping_reply_text())


async def cmd_help(message: Message) -> None:
    """
    Справка для пользователей (без админских команд).
    """
    await message.answer(
        "Справка по командам:\n"
        "- /ping — проверка бота\n"
        "- /share_phone — передать телефон для профиля\n"
        "- /sd_open — показать открытые заявки\n"
        "- /reset_password — сбросить пароль в SD\n"
        "- /get_link — ссылка на загрузку логов"
    )


async def cmd_help_admin(message: Message) -> None:
    """
    Справка для админов.
    """
    await message.answer(
        "Админские команды:\n"
        "- /status\n"
        "- /needs_web\n"
        "- /routes_test\n"
        "- /routes_debug\n"
        "- /routes_send_test\n"
        "- /escalation_send_test\n"
        "- /user_add <id>\n"
        "- /user_remove <id>\n"
        "- /admin_add <id>\n"
        "- /user_list [admins|users] [history]\n"
        "- /user_list top10\n"
        "- /user_history <id> [limit]\n"
        "- /user_audit <id> [limit]\n"
        "- /share_contact <id> <phone>\n"
        "- /config_diff <from> <to>\n"
        "- /last_eventlog_id [set <id>]\n"
        "- /eventlog_poll\n"
        "- /help_admin"
    )


async def cmd_status(
    message: Message,
    web_client: WebClient,
    polling_state: PollingState,
    state_store: Optional[StateStore],
    runtime_config: RuntimeConfig,
) -> None:
    env = get_env("ENVIRONMENT", "unknown")
    version, version_source = get_version_info()
    web_base_url = get_env("WEB_BASE_URL", "http://web:8000")

    if state_store is not None:
        ping_fn = getattr(state_store, "ping", None)
        if callable(ping_fn):
            with contextlib.suppress(Exception):
                ping_fn()

    store_backend = state_store.backend() if state_store is not None else "disabled"
    store_last_error = getattr(state_store, "last_error", None) if state_store is not None else None
    store_last_ok_ts = getattr(state_store, "last_ok_ts", None) if state_store is not None else None

    health, ready = await web_client.check_health_ready(force=True)

    lines = [
        f"ENVIRONMENT: {env}",
        f"VERSION: {version}",
        f"VERSION_SOURCE: {version_source}",
        f"WEB_BASE_URL: {web_base_url}",
        "",
        "STATE STORE:",
        f"- enabled: {'yes' if state_store is not None else 'no'}",
        f"- backend: {store_backend}",
        f"- last_redis_ok: {_fmt_ts(store_last_ok_ts) if store_last_ok_ts else '—'}",
        f"- last_redis_error: {store_last_error or '—'}",
        "",
        _format_check_line("web.health", health.ok, health.status, health.duration_ms, health.request_id, health.error),
        _format_check_line("web.ready", ready.ok, ready.status, ready.duration_ms, ready.request_id, ready.error),
        "",
        "CONFIG:",
        f"- source: {runtime_config.source}",
        f"- version: {runtime_config.version}",
        f"- routing.rules: {len(runtime_config.routing.rules)}",
        f"- escalation.enabled: {'yes' if runtime_config.escalation.enabled else 'no'}",
        "",
        "SD QUEUE POLLING:",
        f"- runs: {polling_state.runs}",
        f"- failures: {polling_state.failures} (consecutive={polling_state.consecutive_failures})",
        f"- last_run: {_fmt_ts(polling_state.last_run_ts)}",
        f"- last_success: {_fmt_ts(polling_state.last_success_ts)}",
        f"- last_error: {polling_state.last_error or '—'}",
        f"- last_duration_ms: {polling_state.last_duration_ms if polling_state.last_duration_ms is not None else '—'}",
        "",
        "NOTIFY RATE-LIMIT:",
        f"- last_notify_attempt_at: {_fmt_ts(polling_state.last_notify_attempt_at)}",
        f"- notify_skipped_rate_limit: {polling_state.notify_skipped_rate_limit}",
        "",
        "ROUTING OBSERVABILITY:",
        f"- tickets_without_destination_total: {getattr(polling_state, 'tickets_without_destination_total', 0)}",
        f"- last_ticket_without_destination_at: {_fmt_ts(getattr(polling_state, 'last_ticket_without_destination_at', None))}",
        f"- last_admin_alert_at: {_fmt_ts(getattr(polling_state, 'last_admin_alert_at', None))}",
        f"- admin_alerts_skipped_rate_limit: {getattr(polling_state, 'admin_alerts_skipped_rate_limit', 0)}",
        "",
        "OBSERVABILITY (27B/27D):",
        f"- last_web_alert_at: {_fmt_ts(getattr(polling_state, 'last_web_alert_at', None))}",
        f"- web_alerts_skipped_rate_limit: {getattr(polling_state, 'web_alerts_skipped_rate_limit', 0)}",
        f"- last_redis_alert_at: {_fmt_ts(getattr(polling_state, 'last_redis_alert_at', None))}",
        f"- redis_alerts_skipped_rate_limit: {getattr(polling_state, 'redis_alerts_skipped_rate_limit', 0)}",
        f"- last_rollback_alert_at: {_fmt_ts(getattr(polling_state, 'last_rollback_alert_at', None))}",
        f"- rollback_alerts_skipped_rate_limit: {getattr(polling_state, 'rollback_alerts_skipped_rate_limit', 0)}",
    ]
    await message.answer("\n".join(lines))


async def cmd_needs_web(message: Message) -> None:
    await message.answer("web готов ✅")


async def cmd_sd_open(message: Message, sd_web_client: SdWebClient) -> None:
    res = await sd_web_client.get_open(limit=20)
    if not res.ok:
        rid = f"\nrequest_id={res.request_id}" if res.request_id else ""
        await message.answer(f"❌ Не удалось получить заявки из ServiceDesk.{rid}\nПричина: {res.error}")
        return

    if not res.items:
        await message.answer("📌 Открытых заявок нет ✅")
        return

    lines = [f"📌 Открытые заявки: {res.count_returned}", ""]
    for t in res.items[:20]:
        lines.append(f"- #{t.get('Id')}: {t.get('Name')}")
    await message.answer("\n".join(lines))


async def cmd_routes_test(message: Message, config_sync: ConfigSyncService, runtime_config: RuntimeConfig) -> None:
    args = _parse_kv_args(message.text or "")
    name = args.get("name", "test ticket")
    service_id = _to_int(args.get("service_id", "")) if "service_id" in args else None
    customer_id = _to_int(args.get("customer_id", "")) if "customer_id" in args else None

    # Подтягиваем конфиг (TTL-кэш внутри клиента). Ошибка не должна ломать команду.
    await config_sync.refresh(force=False)

    routing = runtime_config.routing
    fake = _build_fake_item(
        name=name,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
        service_id=service_id,
        customer_id=customer_id,
    )
    dests = pick_destinations(
        items=[fake],
        rules=routing.rules,
        default_dest=routing.default_dest,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
    )

    lines = [
        "🧪 routes_test",
        f"- Name: {name}",
        f"- {routing.service_id_field}: {service_id if service_id is not None else '—'}",
        f"- {routing.customer_id_field}: {customer_id if customer_id is not None else '—'}",
        f"- rules: {len(routing.rules)}",
        f"- config: v{runtime_config.version} ({runtime_config.source})",
        "",
        "Destinations:",
    ]
    if not dests:
        lines.append("— (ничего; default_dest тоже не задан)")
    else:
        for d in dests:
            lines.append(f"- chat_id={d.chat_id}, thread_id={d.thread_id if d.thread_id is not None else '—'}")

    await message.answer("\n".join(lines))


async def cmd_routes_debug(message: Message, config_sync: ConfigSyncService, runtime_config: RuntimeConfig) -> None:
    args = _parse_kv_args(message.text or "")
    name = args.get("name", "test ticket")
    service_id = _to_int(args.get("service_id", "")) if "service_id" in args else None
    customer_id = _to_int(args.get("customer_id", "")) if "customer_id" in args else None

    await config_sync.refresh(force=False)

    routing = runtime_config.routing

    fake = _build_fake_item(
        name=name,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
        service_id=service_id,
        customer_id=customer_id,
    )

    debug = explain_matches(
        items=[fake],
        rules=routing.rules,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
    )

    lines = [
        "🔎 routes_debug",
        f"- Name: {name}",
        f"- {routing.service_id_field}: {service_id if service_id is not None else '—'}",
        f"- {routing.customer_id_field}: {customer_id if customer_id is not None else '—'}",
        f"- rules: {len(routing.rules)}",
        f"- config: v{runtime_config.version} ({runtime_config.source})",
        "",
    ]

    for r in debug:
        idx = r["index"]
        dest = r["dest"]
        matched = "✅ matched" if r["matched"] else "❌ not matched"
        reason = r["reason"] or "—"
        lines.append(
            f"{idx}) {matched} -> chat_id={dest['chat_id']}, thread_id={dest['thread_id'] if dest['thread_id'] is not None else '—'}"
        )
        lines.append(f"   reason: {reason}")

    await message.answer("\n".join(lines))


async def cmd_routes_send_test(
    message: Message,
    bot: Bot,
    config_sync: ConfigSyncService,
    runtime_config: RuntimeConfig,
) -> None:
    args = _parse_kv_args(message.text or "")
    name = args.get("name", "test ticket")
    service_id = _to_int(args.get("service_id", "")) if "service_id" in args else None
    customer_id = _to_int(args.get("customer_id", "")) if "customer_id" in args else None

    await config_sync.refresh(force=False)

    routing = runtime_config.routing

    fake = _build_fake_item(
        name=name,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
        service_id=service_id,
        customer_id=customer_id,
    )

    dests = pick_destinations(
        items=[fake],
        rules=routing.rules,
        default_dest=routing.default_dest,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
    )

    if not dests:
        await message.answer("❌ Destinations пустой (нет default_dest и не сработали правила)")
        return

    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
    text = (
        "🧪 TEST MESSAGE (routes)\n"
        f"Time: {ts}\n"
        f"Name: {name}\n"
        f"{routing.service_id_field}: {service_id if service_id is not None else '—'}\n"
        f"{routing.customer_id_field}: {customer_id if customer_id is not None else '—'}\n"
        "Если вы это видите — доставка в этот destination работает ✅"
    )

    sent = 0
    failed: list[str] = []
    for d in dests:
        try:
            await bot.send_message(chat_id=d.chat_id, message_thread_id=d.thread_id, text=text)
            sent += 1
        except Exception as e:
            failed.append(f"chat_id={d.chat_id}, thread_id={d.thread_id if d.thread_id is not None else '—'} -> {e}")

    lines = ["📨 routes_send_test result", f"- destinations: {len(dests)}", f"- sent: {sent}"]
    if failed:
        lines.append(f"- failed: {len(failed)}")
        lines.append("")
        lines.extend(failed)

    await message.answer("\n".join(lines))


async def cmd_escalation_send_test(
    message: Message,
    bot: Bot,
    config_sync: ConfigSyncService,
    runtime_config: RuntimeConfig,
) -> None:
    """
    /escalation_send_test name="VIP авария" service_id=101 customer_id=5001

    Реально отправляет тестовое эскалационное сообщение в ESCALATION_DEST.
    Перед отправкой проверяет, проходит ли заявка через ESCALATION_FILTER.
    (Порог времени after_s здесь НЕ ждём — цель команды проверить доставку и конфиг.)
    """
    args = _parse_kv_args(message.text or "")
    name = args.get("name", "test ticket")
    service_id = _to_int(args.get("service_id", "")) if "service_id" in args else None
    customer_id = _to_int(args.get("customer_id", "")) if "customer_id" in args else None

    # Подтягиваем актуальный конфиг (TTL-кэш внутри клиента).
    await config_sync.refresh(force=False)

    esc = runtime_config.escalation
    if not esc.enabled:
        await message.answer("❌ Эскалация отключена (escalation.enabled=false)")
        return

    if esc.dest is None:
        await message.answer("❌ escalation.dest не задан (chat_id обязателен)")
        return

    fake = _build_fake_item(
        name=name,
        service_id_field=esc.service_id_field,
        customer_id_field=esc.customer_id_field,
        service_id=service_id,
        customer_id=customer_id,
    )

    matched = _match_escalation_filter(fake, esc.flt, esc.service_id_field, esc.customer_id_field)
    if not matched:
        await message.answer(
            "⚠️ Заявка НЕ проходит ESCALATION_FILTER, поэтому тестовое сообщение НЕ отправляю.\n"
            f"Параметры:\n- Name={name}\n- {esc.service_id_field}={service_id}\n- {esc.customer_id_field}={customer_id}\n"
            f"Фильтр: keywords={list(esc.flt.keywords)} service_ids={list(esc.flt.service_ids)} customer_ids={list(esc.flt.customer_ids)}"
        )
        return

    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
    text = (
        "🚨 TEST MESSAGE (escalation)\n"
        f"Time: {ts}\n"
        f"After_s (config): {esc.after_s}\n"
        f"{esc.mention} заберите в работу, пожалуйста.\n"
        "\n"
        f"- #{fake.get('Id')}: {fake.get('Name')}\n"
        f"- {esc.service_id_field}: {service_id if service_id is not None else '—'}\n"
        f"- {esc.customer_id_field}: {customer_id if customer_id is not None else '—'}\n"
        "\n"
        "Если вы это видите — доставка эскалации в ESCALATION_DEST работает ✅"
    )

    try:
        await bot.send_message(chat_id=esc.dest.chat_id, message_thread_id=esc.dest.thread_id, text=text)
        await message.answer(
            "📨 escalation_send_test: отправлено ✅\n"
            f"- dest chat_id={esc.dest.chat_id}, thread_id={esc.dest.thread_id if esc.dest.thread_id is not None else '—'}\n"
            f"- config: v{runtime_config.version} ({runtime_config.source})"
        )
    except Exception as e:
        await message.answer(
            "❌ escalation_send_test: не удалось отправить\n"
            f"- dest chat_id={esc.dest.chat_id}, thread_id={esc.dest.thread_id if esc.dest.thread_id is not None else '—'}\n"
            f"- error: {e}"
        )


async def cmd_share_phone(message: Message, user_store: UserStore) -> None:
    """
    Просит пользователя отправить контакт (номер телефона).
    """
    # Если номер передали аргументом — сохраняем сразу.
    phone_arg = _parse_phone_arg(message)
    if phone_arg:
        if message.from_user is None:
            return
        role = await user_store.get_role(message.from_user.id)
        if role is None:
            await message.answer("⛔ Вы не зарегистрированы. Обратитесь к администратору.")
            return
        if role != "admin":
            await message.answer(
                "⛔ Ручной ввод телефона доступен только администратору.\n"
                "Используйте кнопку отправки контакта."
            )
            return
        await message.answer(
            "Используйте команду /share_contact <id> <phone> для ручного ввода телефона."
        )
        return

    if message.chat.type != "private":
        await message.answer(
            "⚠️ Отправка телефона доступна только в личном чате с ботом.\n"
            "Напишите боту в личку и используйте /share_phone там."
        )
        return

    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить телефон", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    try:
        await message.answer(
            "Нажмите кнопку ниже, чтобы отправить номер телефона. "
            "Он будет сохранён в вашем профиле.\n"
            "Важно: отправка контакта доступна только в личном чате с ботом.",
            reply_markup=kb,
        )
    except TelegramBadRequest:
        await message.answer(
            "⚠️ Отправка телефона доступна только в личном чате с ботом.\n"
            "Напишите боту в личку и используйте /share_phone там."
        )


async def cmd_save_contact(message: Message, user_store: UserStore, sd_api_client: SdApiClient) -> None:
    """
    Сохраняет телефон из contact-сообщения.
    """
    if message.contact is None or message.from_user is None:
        return
    if message.contact.user_id != message.from_user.id:
        await message.answer("Можно сохранить только свой номер.")
        return
    role = await user_store.get_role(message.from_user.id)
    if role is None:
        await message.answer("⛔ Вы не зарегистрированы. Обратитесь к администратору.")
        return
    profile = _profile_from_message(message)
    await user_store.upsert_profile(profile, role=role)
    await user_store.log_audit(
        telegram_id=profile.telegram_id,
        action="U:share_phone_contact",
        actor_id=profile.telegram_id,
    )
    await message.answer("✅ Телефон сохранён.", reply_markup=ReplyKeyboardRemove())

    pending = _get_pending_reset_password(profile.telegram_id)
    if pending is not None:
        _clear_pending_reset_password(profile.telegram_id)
        await _reset_password_flow(message, sd_api_client, profile.phone)


async def cmd_reset_password(message: Message, user_store: UserStore, sd_api_client: SdApiClient) -> None:
    """
    Ищет пользователя по телефону и запускает сброс пароля.
    """
    if message.from_user is None:
        return
    profile = await user_store.get_profile(message.from_user.id)
    phone = profile.phone if profile else ""
    if not phone:
        if message.chat.type != "private":
            await message.answer(
                "⚠️ Номер не найден. Отправка телефона доступна только в личном чате с ботом.\n"
                "Напишите боту в личку и используйте /share_phone там."
            )
            return
        _set_pending_reset_password(message.from_user.id)
        await cmd_share_phone(message, user_store)
        return

    await _reset_password_flow(message, sd_api_client, phone)


async def _reset_password_flow(message: Message, sd_api_client: SdApiClient, phone: str) -> None:
    phone_norm = _normalize_phone(phone)
    await message.answer("🔍 Выполняется поиск пользователей по мобильному номеру...")
    try:
        found_users = await asyncio.to_thread(sd_api_client.find_users_by_phone, phone_norm)
    except Exception as e:
        await message.answer(f"⚠️ Произошла ошибка: {e}")
        return

    if not found_users:
        await message.answer(f"❌ Пользователи не найдены для {phone_norm}")
        return

    builder = InlineKeyboardBuilder()
    for user in found_users:
        text = f"id: {user.get('Id')}, name: {user.get('Name')}"
        builder.row(InlineKeyboardButton(text=text, callback_data=f"rp:{user.get('Id')}"))
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="rp:cancel"))

    await message.answer(
        f"К номеру {phone_norm} привязано {len(found_users)} записей.\n"
        "Выберите запись, для которой нужно сбросить пароль.",
        reply_markup=builder.as_markup(),
    )


async def cmd_share_contact(message: Message, user_store: UserStore) -> None:
    """
    /share_contact <telegram_id> <phone>

    Ручной ввод телефона админом для указанного пользователя.
    """
    target_id = _parse_target_id(message)
    phone = _parse_phone_arg(message)

    if target_id is None and phone is None:
        await message.answer("Формат: /share_contact <telegram_id> <phone> (или ответ на сообщение пользователя)")
        return

    if target_id is None:
        await message.answer("Не удалось определить telegram_id. Ответьте на сообщение пользователя.")
        return

    if phone is None:
        _set_pending_share_contact(message.from_user.id, target_id)
        await message.answer(f"Введите телефон для пользователя {target_id}.")
        return

    role = await user_store.get_role(target_id) or "user"
    profile = TgProfile(
        telegram_id=target_id,
        username="",
        full_name="",
        phone=phone,
    )
    await user_store.upsert_profile(profile, role=role)
    await user_store.log_audit(
        telegram_id=target_id,
        action="U:share_contact_admin",
        actor_id=message.from_user.id if message.from_user else None,
    )
    await message.answer(f"✅ Телефон сохранён для пользователя {target_id}.")


async def cmd_share_contact_phone(message: Message, user_store: UserStore) -> None:
    """
    Принимает телефон для ранее начатого /share_contact без параметров.
    """
    if message.from_user is None or message.text is None:
        return
    pending = _get_pending_share_contact(message.from_user.id)
    if pending is None:
        return
    phone = _parse_phone_text(message.text)
    if phone is None:
        await message.answer("Некорректный телефон. Попробуйте ещё раз.")
        return

    target_id = int(pending["target_id"])
    role = await user_store.get_role(target_id) or "user"
    profile = TgProfile(
        telegram_id=target_id,
        username="",
        full_name="",
        phone=phone,
    )
    await user_store.upsert_profile(profile, role=role)
    await user_store.log_audit(
        telegram_id=target_id,
        action="U:share_contact_admin",
        actor_id=message.from_user.id if message.from_user else None,
    )
    _clear_pending_share_contact(message.from_user.id)
    await message.answer(f"✅ Телефон сохранён для пользователя {target_id}.")


async def cmd_get_link(message: Message, state: FSMContext, seafile_store: SeafileServiceStore) -> None:
    """
    Запускает диалог получения ссылки на загрузку логов (Seafile).
    """
    services = await seafile_store.list_services(enabled_only=True)
    if not services:
        await message.answer("❌ Сервисы Seafile не настроены.")
        return

    builder = InlineKeyboardBuilder()
    for svc in services:
        title = svc.name or svc.base_url
        builder.row(InlineKeyboardButton(text=title, callback_data=f"gl:{svc.service_id}"))

    await state.set_state(LinkRequest.waiting_for_service)
    await message.answer("на какой ресурс нужно загрузить лог?", reply_markup=builder.as_markup())


async def cb_get_link_service(callback: CallbackQuery, state: FSMContext, user_store: UserStore) -> None:
    """
    Сохраняет выбранный сервис и запрашивает номер тикета.
    """
    if callback.from_user is None:
        return
    role = await user_store.get_role(callback.from_user.id)
    if role not in ("user", "admin"):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    data = callback.data or ""
    if not data.startswith("gl:"):
        return
    try:
        service_id = int(data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректный сервис", show_alert=True)
        return

    await state.update_data(service_id=service_id)
    await state.set_state(LinkRequest.waiting_for_ticket)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Введите номер тикета:")
    await callback.answer()


async def cmd_get_link_ticket(
    message: Message,
    state: FSMContext,
    seafile_store: SeafileServiceStore,
) -> None:
    """
    Обрабатывает номер тикета и возвращает ссылку.
    """
    if not message.text:
        return
    ticket = message.text.strip()
    if not ticket.isdigit():
        await message.answer(
            "Некорректный ввод.\n"
            "В комментарии должен быть только номер тикета из цифр."
        )
        return

    data = await state.get_data()
    service_id = data.get("service_id")
    if service_id is None:
        await message.answer("Ошибка: не выбран сервис.")
        await state.clear()
        return

    service = await seafile_store.get_service(int(service_id))
    if service is None or not service.enabled:
        await message.answer("Ошибка: сервис не найден или выключен.")
        await state.clear()
        return

    try:
        link = await asyncio.to_thread(getlink, ticket, service)
    except Exception as e:
        await message.answer(f"❌ Не удалось создать ссылку: {e}")
        await state.clear()
        return
    if link == "err":
        await message.answer("❌ Не удалось создать ссылку.")
    else:
        await message.answer(link)
    await state.clear()


async def cb_reset_password(callback: CallbackQuery, user_store: UserStore, sd_api_client: SdApiClient) -> None:
    if callback.from_user is None:
        return
    role = await user_store.get_role(callback.from_user.id)
    if role not in ("user", "admin"):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    data = callback.data or ""
    if data == "rp:cancel":
        return

    try:
        user_id = int(data.split(":", 1)[1])
    except Exception:
        await callback.answer("Некорректный идентификатор", show_alert=True)
        return

    try:
        answer = await asyncio.to_thread(sd_api_client.reset_user_password, user_id)
    except Exception as e:
        if callback.message:
            await callback.message.answer(f"⚠️ Произошла ошибка: {e}")
        await callback.answer()
        return
    formatted_json = json.dumps(answer, indent=4, ensure_ascii=False)
    text = f"<b>📄 Вот ваш JSON:</b>\n<pre><code>{formatted_json}</code></pre>"
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(text, parse_mode="HTML")
        new_password = answer.get("new_password")
        if new_password:
            await callback.message.answer(
                f"Скопировать пароль в буфер обмена:\n<code>{new_password}</code>",
                parse_mode="HTML",
            )
    await callback.answer()


async def cb_reset_password_cancel(callback: CallbackQuery, user_store: UserStore) -> None:
    if callback.from_user is None:
        return
    role = await user_store.get_role(callback.from_user.id)
    if role not in ("user", "admin"):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Сброс пароля отменен пользователем.")
    await callback.answer()


async def cmd_last_eventlog_id(message: Message, state_store: StateStore) -> None:
    """
    /last_eventlog_id
    /last_eventlog_id set <id>
    """
    parts = (message.text or "").split()
    if state_store is None:
        await message.answer("State store отключен.")
        return

    if len(parts) == 1:
        data = state_store.get_json(EVENTLOG_STATE_KEY) or {}
        last_id = data.get("last_event_id")
        if last_id is None:
            await message.answer("Последний eventlog id: —")
        else:
            await message.answer(f"Последний eventlog id: {last_id}")
        return

    if len(parts) >= 3 and parts[1].lower() == "set":
        try:
            new_id = int(parts[2])
        except Exception:
            await message.answer("Формат: /last_eventlog_id set <id>")
            return
        state_store.set_json(EVENTLOG_STATE_KEY, {"last_event_id": new_id, "updated_at": time.time()})
        await message.answer(f"✅ last_eventlog_id обновлён: {new_id}")
        return

    await message.answer("Формат: /last_eventlog_id или /last_eventlog_id set <id>")


async def cmd_eventlog_poll(
    message: Message,
    state_store: StateStore,
    eventlog_filter_store,
    eventlog_login: str,
    eventlog_password: str,
    eventlog_base_url: str,
    eventlog_start_id: int,
    notify_eventlog,
) -> None:
    """
    Принудительный одиночный прогон eventlog.
    """
    res = await eventlog_poll_once(
        notify_eventlog=notify_eventlog,
        store=state_store,
        filter_store=eventlog_filter_store,
        login=eventlog_login,
        password=eventlog_password,
        base_url=eventlog_base_url,
        start_event_id=eventlog_start_id,
    )

    ok = res.get("ok")
    status = res.get("status")
    next_id = res.get("next_id")
    bootstrapped = res.get("bootstrapped")
    last_item = res.get("last_item")
    err = res.get("error") or res.get("reason")
    parse_error = res.get("parse_error")
    lines = [f"eventlog_poll: {'ok' if ok else 'fail'}", f"status: {status}"]
    if next_id is not None:
        lines.append(f"next_id: {next_id}")
    if bootstrapped is not None:
        lines.append(f"bootstrapped: {bootstrapped}")
    if last_item is not None:
        lines.append(f"last_item: {last_item}")
    if err:
        lines.append(f"error: {err}")
    if parse_error:
        lines.append(f"parse_error: {parse_error}")
    await message.answer("\n".join(lines))


async def cmd_user_add(message: Message, user_store: UserStore) -> None:
    """
    /user_add <telegram_id>

    Добавляет пользователя с ролью user.
    Можно использовать ответ на сообщение — тогда id возьмём из reply.
    """
    target_id = _parse_target_id(message)
    if target_id is None:
        await message.answer("Формат: /user_add <telegram_id> (или ответ на сообщение пользователя)")
        return

    await user_store.upsert_role(
        telegram_id=target_id,
        role="user",
        added_by=message.from_user.id if message.from_user else None,
    )
    await user_store.log_audit(
        telegram_id=target_id,
        action="U:user_add",
        actor_id=message.from_user.id if message.from_user else None,
    )
    await _maybe_update_profile_from_reply(message, user_store)
    await message.answer(f"✅ Пользователь добавлен: {target_id}")


async def cmd_user_remove(message: Message, user_store: UserStore) -> None:
    """
    /user_remove <telegram_id>

    Снимает права пользователя (удаляет запись).
    """
    target_id = _parse_target_id(message)
    if target_id is None:
        await message.answer("Формат: /user_remove <telegram_id> (или ответ на сообщение пользователя)")
        return

    await user_store.delete_user(target_id)
    await user_store.log_audit(
        telegram_id=target_id,
        action="D:user_remove",
        actor_id=message.from_user.id if message.from_user else None,
    )
    await message.answer(f"✅ Пользователь удалён: {target_id}")


async def cmd_admin_add(message: Message, user_store: UserStore) -> None:
    """
    /admin_add <telegram_id>

    Добавляет пользователя с ролью admin.
    """
    target_id = _parse_target_id(message)
    if target_id is None:
        await message.answer("Формат: /admin_add <telegram_id> (или ответ на сообщение пользователя)")
        return

    await user_store.upsert_role(
        telegram_id=target_id,
        role="admin",
        added_by=message.from_user.id if message.from_user else None,
    )
    await user_store.log_audit(
        telegram_id=target_id,
        action="U:admin_add",
        actor_id=message.from_user.id if message.from_user else None,
    )
    await _maybe_update_profile_from_reply(message, user_store)
    await message.answer(f"✅ Админ добавлен: {target_id}")


async def cmd_user_list(message: Message, user_store: UserStore) -> None:
    """
    /user_list

    Показывает список пользователей и админов.
    """
    role_filter = _parse_role_filter(message)
    show_history = _parse_history_flag(message)
    top10 = _parse_top10_flag(message)

    if top10:
        await _render_top10(message, user_store)
        return

    items = await user_store.list_users(limit=200)
    if not items:
        await message.answer("Список пользователей пуст.", reply_markup=ReplyKeyboardRemove())
        return

    if role_filter:
        items = [it for it in items if it.get("role") == role_filter]

    title = "Админы" if role_filter == "admin" else "Пользователи"
    if role_filter is None:
        title = "Пользователи и админы"

    lines = [f"{title} (до 200):"]
    header = _format_user_row(
        role="role",
        telegram_id="id",
        username="username",
        full_name="name",
        phone="phone",
        last_info="last",
        show_history=show_history,
        is_header=True,
    )
    lines.append("```")
    lines.append(header)
    for it in items:
        role = it.get("role")
        tid = it.get("telegram_id")
        username = it.get("username") or ""
        username_part = f"@{username}" if username else "—"
        full_name = it.get("full_name") or "—"
        phone = it.get("phone") or "—"
        last_info = ""
        if show_history:
            last_cmd = it.get("last_command") or "—"
            last_at = it.get("last_command_at")
            last_at_s = last_at.strftime("%Y-%m-%d %H:%M:%S") if last_at else "—"
            last_info = f"{last_cmd} @ {last_at_s}"
        lines.append(
            _format_user_row(
                role=str(role),
                telegram_id=str(tid),
                username=username_part,
                full_name=full_name,
                phone=phone,
                last_info=last_info,
                show_history=show_history,
                is_header=False,
            )
        )
    lines.append("```")

    await message.answer("\n".join(lines), reply_markup=ReplyKeyboardRemove())


async def cmd_user_history(message: Message, user_store: UserStore) -> None:
    """
    /user_history <telegram_id> [limit]

    Показывает историю команд пользователя.
    """
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /user_history <telegram_id> [limit]")
        return
    try:
        target_id = int(parts[1])
    except Exception:
        await message.answer("Некорректный telegram_id.")
        return

    limit = 20
    if len(parts) >= 3:
        try:
            limit = max(1, min(int(parts[2]), 200))
        except Exception:
            limit = 20

    items = await user_store.list_history(target_id, limit=limit)
    if not items:
        await message.answer("История команд пустая.")
        return

    lines = [f"История команд для {target_id} (до {limit}):"]
    for it in items:
        cmd = it.get("command") or "—"
        ts = it.get("created_at")
        ts_s = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "—"
        lines.append(f"- {ts_s} {cmd}")

    await message.answer("\n".join(lines), reply_markup=ReplyKeyboardRemove())


async def cmd_user_audit(message: Message, user_store: UserStore) -> None:
    """
    /user_audit <telegram_id> [limit]

    Показывает audit-историю по пользователю.
    """
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /user_audit <telegram_id> [limit]")
        return
    try:
        target_id = int(parts[1])
    except Exception:
        await message.answer("Некорректный telegram_id.")
        return

    limit = 20
    if len(parts) >= 3:
        try:
            limit = max(1, min(int(parts[2]), 200))
        except Exception:
            limit = 20

    items = await user_store.list_audit(target_id, limit=limit)
    if not items:
        await message.answer("Audit-история пустая.")
        return

    lines = [f"Audit для {target_id} (до {limit}):"]
    for it in items:
        action = it.get("action") or "—"
        actor = it.get("actor_id")
        actor_s = str(actor) if actor is not None else "—"
        ts = it.get("created_at")
        ts_s = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "—"
        lines.append(f"- {ts_s} {action} (actor={actor_s})")

    await message.answer("\n".join(lines), reply_markup=ReplyKeyboardRemove())


async def cmd_config_diff(message: Message, web_client: WebClient, config_admin_token: str) -> None:
    """
    /config_diff <from> <to>

    Показывает diff между версиями конфига.
    """
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Формат: /config_diff <from> <to>")
        return
    try:
        v_from = int(parts[1])
        v_to = int(parts[2])
    except Exception:
        await message.answer("Некорректные версии.")
        return

    res = await web_client.get_config_diff(v_from=v_from, v_to=v_to, admin_token=config_admin_token)
    if not res.get("ok"):
        await message.answer(f"❌ Не удалось получить diff: {res.get('error')}")
        return
    data = res.get("data", {})
    changes = data.get("changes") or []
    if not changes:
        await message.answer("Изменений нет.")
        return

    lines = [f"Diff {v_from} -> {v_to} (первые 20):"]
    for ch in changes[:20]:
        path = ch.get("path")
        frm = ch.get("from")
        to = ch.get("to")
        lines.append(f"- {path}: {frm} -> {to}")
    await message.answer("\n".join(lines))


def _parse_target_id(message: Message) -> Optional[int]:
    """
    Берём id из аргумента команды или из reply.
    """
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id

    parts = (message.text or "").split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except Exception:
        return None


def _parse_role_filter(message: Message) -> Optional[str]:
    """
    Парсим фильтр для /user_list: admins|users.
    """
    parts = (message.text or "").split()
    if len(parts) < 2:
        return None
    arg = parts[1].strip().lower()
    if arg in {"admin", "admins"}:
        return "admin"
    if arg in {"user", "users"}:
        return "user"
    return None


def _parse_history_flag(message: Message) -> bool:
    """
    Проверяет наличие флага history в /user_list.
    """
    parts = (message.text or "").split()
    return any(p.strip().lower() == "history" for p in parts[1:])


def _parse_top10_flag(message: Message) -> bool:
    parts = (message.text or "").split()
    return any(p.strip().lower() == "top10" for p in parts[1:])


def _format_user_row(
    *,
    role: str,
    telegram_id: str,
    username: str,
    full_name: str,
    phone: str,
    last_info: str,
    show_history: bool,
    is_header: bool,
) -> str:
    """
    Ровная строка таблицы для /user_list.
    """
    def _cut(s: str, n: int) -> str:
        s = s.replace("\n", " ")
        return s if len(s) <= n else s[: n - 1] + "…"

    role_w = 6
    id_w = 12
    user_w = 20
    name_w = 22
    phone_w = 16
    last_w = 28

    role_s = _cut(role, role_w).ljust(role_w)
    id_s = _cut(telegram_id, id_w).ljust(id_w)
    user_s = _cut(username, user_w).ljust(user_w)
    name_s = _cut(full_name, name_w).ljust(name_w)
    phone_s = _cut(phone, phone_w).ljust(phone_w)

    if show_history:
        last_s = _cut(last_info or "—", last_w).ljust(last_w)
        return f"{role_s} {id_s} {user_s} {name_s} {phone_s} {last_s}"

    return f"{role_s} {id_s} {user_s} {name_s} {phone_s}"


async def _render_top10(message: Message, user_store: UserStore) -> None:
    """
    Рендерит топ-10 по последнему обращению и по частоте.
    """
    last_activity = await user_store.top_by_last_activity(limit=10)
    by_freq = await user_store.top_by_frequency(limit=10)

    lines = ["Топ-10 пользователей:"]

    lines.append("")
    lines.append("По последнему обращению:")
    if not last_activity:
        lines.append("— нет данных")
    else:
        for it in last_activity:
            username = it.get("username") or ""
            username_part = f"@{username}" if username else "—"
            full_name = it.get("full_name") or "—"
            last_cmd = it.get("last_command") or "—"
            last_at = it.get("last_command_at")
            last_at_s = last_at.strftime("%Y-%m-%d %H:%M:%S") if last_at else "—"
            lines.append(f"- {it['telegram_id']} ({username_part}) {full_name} | {last_cmd} @ {last_at_s}")

    lines.append("")
    lines.append("По частоте обращений:")
    if not by_freq:
        lines.append("— нет данных")
    else:
        for it in by_freq:
            username = it.get("username") or ""
            username_part = f"@{username}" if username else "—"
            full_name = it.get("full_name") or "—"
            count = it.get("count", 0)
            last_seen = it.get("last_seen")
            last_seen_s = last_seen.strftime("%Y-%m-%d %H:%M:%S") if last_seen else "—"
            lines.append(f"- {it['telegram_id']} ({username_part}) {full_name} | {count} | last: {last_seen_s}")

    await message.answer("\n".join(lines), reply_markup=ReplyKeyboardRemove())


async def _maybe_update_profile_from_reply(message: Message, user_store: UserStore) -> None:
    """
    Если команда выполнена в ответ на сообщение пользователя,
    обновляем его профиль на основании reply.
    """
    if not message.reply_to_message:
        return
    reply_msg = message.reply_to_message
    if not reply_msg.from_user:
        return
    profile = _profile_from_message(reply_msg)
    await user_store.update_profile_if_exists(profile)
    await user_store.log_audit(
        telegram_id=profile.telegram_id,
        action="U:update_profile_reply",
        actor_id=message.from_user.id if message.from_user else None,
    )


def _profile_from_message(message: Message) -> TgProfile:
    """
    Извлекает профиль пользователя из сообщения.
    """
    user = message.from_user
    username = user.username or ""
    full_name = " ".join([x for x in [user.first_name, user.last_name] if x]).strip()
    phone = ""
    if message.contact and message.contact.user_id == user.id:
        phone = message.contact.phone_number or ""

    return TgProfile(
        telegram_id=user.id,
        username=username,
        full_name=full_name,
        phone=phone,
    )


def _parse_phone_arg(message: Message) -> Optional[str]:
    """
    Парсит номер телефона из аргумента /share_phone 79990001122.
    """
    parts = (message.text or "").split()
    if len(parts) < 2:
        return None
    phone = parts[1].strip()
    if not phone:
        return None
    return phone


def _parse_phone_text(text: str) -> Optional[str]:
    """
    Парсит телефон из обычного текста.
    """
    phone = text.strip()
    if not phone:
        return None
    return phone


def _set_pending_share_contact(admin_id: int, target_id: int) -> None:
    _PENDING_SHARE_CONTACT[admin_id] = {
        "target_id": target_id,
        "expires_at": time.time() + 300,
    }


def _get_pending_share_contact(admin_id: int) -> Optional[dict[str, object]]:
    item = _PENDING_SHARE_CONTACT.get(admin_id)
    if not item:
        return None
    if float(item.get("expires_at", 0)) < time.time():
        _PENDING_SHARE_CONTACT.pop(admin_id, None)
        return None
    return item


def _clear_pending_share_contact(admin_id: int) -> None:
    _PENDING_SHARE_CONTACT.pop(admin_id, None)


def _is_pending_share_contact(message: Message) -> bool:
    """
    Фильтр для ввода телефона админом без параметров.
    """
    if message.text is None or message.from_user is None:
        return False
    if message.text.strip().startswith("/"):
        return False
    return _get_pending_share_contact(message.from_user.id) is not None


def _normalize_phone(phone: str) -> str:
    phone = phone.strip()
    if phone.startswith("+7"):
        return "8" + phone[2:]
    if phone.startswith("79"):
        return "8" + phone[1:]
    return phone


def _set_pending_reset_password(user_id: int) -> None:
    _PENDING_RESET_PASSWORD[user_id] = {
        "expires_at": time.time() + 300,
    }


def _get_pending_reset_password(user_id: int) -> Optional[dict[str, object]]:
    item = _PENDING_RESET_PASSWORD.get(user_id)
    if not item:
        return None
    if float(item.get("expires_at", 0)) < time.time():
        _PENDING_RESET_PASSWORD.pop(user_id, None)
        return None
    return item


def _clear_pending_reset_password(user_id: int) -> None:
    _PENDING_RESET_PASSWORD.pop(user_id, None)
