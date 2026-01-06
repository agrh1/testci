"""
Командные обработчики бота.

Содержит пользовательские и админские команды.
"""

from __future__ import annotations

import contextlib
import time
from typing import Optional

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from bot import ping_reply_text
from bot.config.settings import get_env
from bot.middlewares.access_control import AccessControlMiddleware, AccessPolicy
from bot.services.config_sync import ConfigSyncService
from bot.services.user_store import TgProfile, UserStore
from bot.utils.escalation import EscalationFilter
from bot.utils.notify_router import explain_matches, pick_destinations
from bot.utils.polling import PollingState
from bot.utils.runtime_config import RuntimeConfig
from bot.utils.sd_web_client import SdWebClient
from bot.utils.state_store import StateStore
from bot.utils.web_client import WebClient
from bot.utils.web_filters import WebReadyFilter


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


async def cmd_start(message: Message) -> None:
    await message.answer(
        "Доступные команды:\n"
        "- /ping\n"
        "- /help\n"
        "- /share_phone (передать телефон для профиля)\n"
        "- /sd_open — показать открытые заявки"
    )


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
        "- /sd_open — показать открытые заявки"
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
        "- /user_list [admins|users]"
    )


async def cmd_status(
    message: Message,
    web_client: WebClient,
    polling_state: PollingState,
    state_store: Optional[StateStore],
    runtime_config: RuntimeConfig,
) -> None:
    env = get_env("ENVIRONMENT", "unknown")
    git_sha = get_env("GIT_SHA", "unknown")
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
        f"GIT_SHA: {git_sha}",
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


async def cmd_share_phone(message: Message) -> None:
    """
    Просит пользователя отправить контакт (номер телефона).
    """
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить телефон", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await message.answer(
        "Нажмите кнопку ниже, чтобы отправить номер телефона. "
        "Он будет сохранён в вашем профиле.",
        reply_markup=kb,
    )


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
    await _maybe_update_profile_from_reply(message, user_store)
    await message.answer(f"✅ Админ добавлен: {target_id}")


async def cmd_user_list(message: Message, user_store: UserStore) -> None:
    """
    /user_list

    Показывает список пользователей и админов.
    """
    role_filter = _parse_role_filter(message)
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
    for it in items:
        role = it.get("role")
        tid = it.get("telegram_id")
        username = it.get("username") or ""
        username_part = f"@{username}" if username else "—"
        full_name = it.get("full_name") or "—"
        phone = it.get("phone") or "—"
        lines.append(f"- {role}: {tid} ({username_part}) {full_name} / {phone}")

    await message.answer("\n".join(lines), reply_markup=ReplyKeyboardRemove())


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
    await user_store.update_profile(profile)


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
