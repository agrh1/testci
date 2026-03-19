"""
Платформо-независимые реализации команд (Mattermost).

Каждая команда принимает CommandRequest и возвращает CommandResponse.
"""

from __future__ import annotations

import time
from typing import Optional

from bot.services.command_executor import CommandRequest, CommandResponse
from bot.services.config_sync import ConfigSyncService
from bot.utils.notify_router import explain_matches, pick_destinations
from bot.utils.runtime_config import RuntimeConfig


def _to_int(x: str) -> Optional[int]:
    """Convert string to int, return None if invalid."""
    try:
        x = x.strip()
        if not x:
            return None
        return int(x)
    except Exception:
        return None


def _get_command_arg(raw_text: str, command: str) -> str:
    """Извлечь аргумент после имени команды из полного текста сообщения.

    Работает корректно как с "@bot cmd arg", так и с "/cmd arg".
    Ищет первый токен равный command (с учётом /) и возвращает всё после него.
    """
    parts = raw_text.strip().split()
    for i, part in enumerate(parts):
        if part.lstrip("/").lower() == command.lower():
            return " ".join(parts[i + 1:]).strip()
    return ""


def _parse_kv_args(text: str) -> dict[str, str]:
    """Parse key=value arguments from command text."""
    parts = text.split()
    out: dict[str, str] = {}
    for p in parts[1:]:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        out[k.strip().lower()] = v.strip()

    # Support name="..." с пробелами внутри
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
    creator_id_field: str,
    creator_company_id_field: str,
    service_id: Optional[int],
    customer_id: Optional[int],
    creator_id: Optional[int],
    creator_company_id: Optional[int],
) -> dict:
    """Build minimal test ticket object."""
    it = {"Id": 999999, "Name": name}
    if service_id is not None:
        it[service_id_field] = service_id
    if customer_id is not None:
        it[customer_id_field] = customer_id
    if creator_id is not None:
        it[creator_id_field] = creator_id
    if creator_company_id is not None:
        it[creator_company_id_field] = creator_company_id
    return it


# ============================================================================
# PHASE 3a: Notification-testing commands (routes_test, routes_debug, etc.)
# ============================================================================


async def cmd_routes_test(
    request: CommandRequest,
    config_sync: ConfigSyncService,
    runtime_config: RuntimeConfig,
) -> CommandResponse:
    """
    Тестирование маршрутизации: показывает куда пойдёт заявка по конфигу.

    /routes_test name="test ticket" service_id=101 customer_id=5001
    """
    args = _parse_kv_args(request.raw_text or "")
    name = args.get("name", "test ticket")
    service_id = _to_int(args.get("service_id", "")) if "service_id" in args else None
    customer_id = _to_int(args.get("customer_id", "")) if "customer_id" in args else None
    creator_id = _to_int(args.get("creator_id", "")) if "creator_id" in args else None
    creator_company_id = _to_int(args.get("creator_company_id", "")) if "creator_company_id" in args else None

    await config_sync.refresh(force=False)

    routing = runtime_config.routing
    fake = _build_fake_item(
        name=name,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
        creator_id_field=routing.creator_id_field,
        creator_company_id_field=routing.creator_company_id_field,
        service_id=service_id,
        customer_id=customer_id,
        creator_id=creator_id,
        creator_company_id=creator_company_id,
    )
    dests = pick_destinations(
        items=[fake],
        rules=routing.rules,
        default_dest=routing.default_dest,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
        creator_id_field=routing.creator_id_field,
        creator_company_id_field=routing.creator_company_id_field,
    )

    lines = [
        "🧪 routes_test",
        f"- Name: {name}",
        f"- {routing.service_id_field}: {service_id if service_id is not None else '—'}",
        f"- {routing.customer_id_field}: {customer_id if customer_id is not None else '—'}",
        f"- {routing.creator_id_field}: {creator_id if creator_id is not None else '—'}",
        f"- {routing.creator_company_id_field}: {creator_company_id if creator_company_id is not None else '—'}",
        f"- rules: {len(routing.rules)}",
        f"- config: v{runtime_config.version} ({runtime_config.source})",
        "",
        "Destinations:",
    ]
    if not dests:
        lines.append("— (ничего; default_dest тоже не задан)")
    else:
        for d in dests:
            lines.append(f"- mattermost: channel={d.destination_id}, root_id={d.thread_id if d.thread_id is not None else '—'}")

    return CommandResponse.success("\n".join(lines))


async def cmd_routes_debug(
    request: CommandRequest,
    config_sync: ConfigSyncService,
    runtime_config: RuntimeConfig,
) -> CommandResponse:
    """
    Подробный debug маршрутизации: показывает для каждого правила сработало ли оно.

    /routes_debug name="test ticket" service_id=101
    """
    args = _parse_kv_args(request.raw_text or "")
    name = args.get("name", "test ticket")
    service_id = _to_int(args.get("service_id", "")) if "service_id" in args else None
    customer_id = _to_int(args.get("customer_id", "")) if "customer_id" in args else None
    creator_id = _to_int(args.get("creator_id", "")) if "creator_id" in args else None
    creator_company_id = _to_int(args.get("creator_company_id", "")) if "creator_company_id" in args else None

    await config_sync.refresh(force=False)

    routing = runtime_config.routing

    fake = _build_fake_item(
        name=name,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
        creator_id_field=routing.creator_id_field,
        creator_company_id_field=routing.creator_company_id_field,
        service_id=service_id,
        customer_id=customer_id,
        creator_id=creator_id,
        creator_company_id=creator_company_id,
    )

    debug = explain_matches(
        items=[fake],
        rules=routing.rules,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
        creator_id_field=routing.creator_id_field,
        creator_company_id_field=routing.creator_company_id_field,
    )

    lines = [
        "🔎 routes_debug",
        f"- Name: {name}",
        f"- {routing.service_id_field}: {service_id if service_id is not None else '—'}",
        f"- {routing.customer_id_field}: {customer_id if customer_id is not None else '—'}",
        f"- {routing.creator_id_field}: {creator_id if creator_id is not None else '—'}",
        f"- {routing.creator_company_id_field}: {creator_company_id if creator_company_id is not None else '—'}",
        f"- rules: {len(routing.rules)}",
        f"- config: v{runtime_config.version} ({runtime_config.source})",
        "",
    ]

    for r in debug:
        idx = r["index"]
        dest = r["dest"]
        matched = "✅ matched" if r["matched"] else "❌ not matched"
        reason = r["reason"] or "—"
        rule_name = r.get("name")
        label = f"{idx})"
        if rule_name:
            label = f"{label} {rule_name}"

        platform_info = f"mattermost: channel={dest['destination_id']}"

        lines.append(f"{label} {matched} -> {platform_info}, thread_id={dest['thread_id'] if dest['thread_id'] is not None else '—'}")
        lines.append(f"   reason: {reason}")

    return CommandResponse.success("\n".join(lines))


async def cmd_routes_send_test(
    request: CommandRequest,
    config_sync: ConfigSyncService,
    runtime_config: RuntimeConfig,
    notification_service,  # NotificationService instance
) -> CommandResponse:
    """
    Отправить тестовое уведомление по маршрутам.

    Реально отправляет в destinations (если они есть).
    """
    args = _parse_kv_args(request.raw_text or "")
    name = args.get("name", "test ticket")
    service_id = _to_int(args.get("service_id", "")) if "service_id" in args else None
    customer_id = _to_int(args.get("customer_id", "")) if "customer_id" in args else None
    creator_id = _to_int(args.get("creator_id", "")) if "creator_id" in args else None
    creator_company_id = _to_int(args.get("creator_company_id", "")) if "creator_company_id" in args else None

    await config_sync.refresh(force=False)

    routing = runtime_config.routing

    fake = _build_fake_item(
        name=name,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
        creator_id_field=routing.creator_id_field,
        creator_company_id_field=routing.creator_company_id_field,
        service_id=service_id,
        customer_id=customer_id,
        creator_id=creator_id,
        creator_company_id=creator_company_id,
    )

    dests = pick_destinations(
        items=[fake],
        rules=routing.rules,
        default_dest=routing.default_dest,
        service_id_field=routing.service_id_field,
        customer_id_field=routing.customer_id_field,
        creator_id_field=routing.creator_id_field,
        creator_company_id_field=routing.creator_company_id_field,
    )

    if not dests:
        return CommandResponse.error("❌ Destinations пустой (нет default_dest и не сработали правила)")

    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
    text = (
        "🧪 TEST MESSAGE (routes)\n"
        f"Time: {ts}\n"
        f"Name: {name}\n"
        f"{routing.service_id_field}: {service_id if service_id is not None else '—'}\n"
        f"{routing.customer_id_field}: {customer_id if customer_id is not None else '—'}\n"
        f"{routing.creator_id_field}: {creator_id if creator_id is not None else '—'}\n"
        f"{routing.creator_company_id_field}: {creator_company_id if creator_company_id is not None else '—'}\n"
        "Если вы это видите — доставка в этот destination работает ✅"
    )

    sent = 0
    failed: list[str] = []
    for d in dests:
        try:
            await notification_service._send_notification_safe(
                destination=d,
                text=text,
                context="routing.test",
            )
            sent += 1
        except Exception as e:
            failed.append(f"mattermost: channel={d.destination_id} -> {e}")

    lines = ["📨 routes_send_test result", f"- destinations: {len(dests)}", f"- sent: {sent}"]
    if failed:
        lines.append(f"- failed: {len(failed)}")
        lines.append("")
        lines.extend(failed)

    return CommandResponse.success("\n".join(lines))


async def cmd_escalation_send_test(
    request: CommandRequest,
    config_sync: ConfigSyncService,
    runtime_config: RuntimeConfig,
    notification_service,  # NotificationService instance
) -> CommandResponse:
    """
    Тестовая отправка эскалационного уведомления.

    /escalation_send_test name="VIP авария" service_id=101 customer_id=5001
    """
    args = _parse_kv_args(request.raw_text or "")
    name = args.get("name", "test ticket")
    service_id = _to_int(args.get("service_id", "")) if "service_id" in args else None
    customer_id = _to_int(args.get("customer_id", "")) if "customer_id" in args else None
    creator_id = _to_int(args.get("creator_id", "")) if "creator_id" in args else None
    creator_company_id = _to_int(args.get("creator_company_id", "")) if "creator_company_id" in args else None

    await config_sync.refresh(force=False)

    esc = runtime_config.escalation
    if not esc.enabled:
        return CommandResponse.error("❌ Эскалация отключена (escalation.enabled=false)")

    if not esc.rules:
        return CommandResponse.error("❌ escalation.rules пустой — нечего тестировать")

    fake = _build_fake_item(
        name=name,
        service_id_field=esc.service_id_field,
        customer_id_field=esc.customer_id_field,
        creator_id_field=esc.creator_id_field,
        creator_company_id_field=esc.creator_company_id_field,
        service_id=service_id,
        customer_id=customer_id,
        creator_id=creator_id,
        creator_company_id=creator_company_id,
    )

    # Get escalations for this item
    escalations = runtime_config.get_escalations([fake])
    if not escalations:
        lines = [
            "⚠️ Заявка не попала ни под одно правило escalation.",
            "Параметры:",
            f"- Name={name}",
            f"- {esc.service_id_field}={service_id if service_id is not None else '—'}",
            f"- {esc.customer_id_field}={customer_id if customer_id is not None else '—'}",
            f"- {esc.creator_id_field}={creator_id if creator_id is not None else '—'}",
            f"- {esc.creator_company_id_field}={creator_company_id if creator_company_id is not None else '—'}",
            f"- rules: {len(esc.rules)}",
        ]
        return CommandResponse.error("\n".join(lines))

    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
    sent = 0
    failed: list[str] = []

    for action in escalations:
        text = (
            "🚨 TEST MESSAGE (escalation)\n"
            f"Time: {ts}\n"
            f"{action.mention} заберите в работу, пожалуйста.\n"
            "\n"
            f"- #{fake.get('Id')}: {fake.get('Name')}\n"
            f"- {esc.service_id_field}: {service_id if service_id is not None else '—'}\n"
            f"- {esc.customer_id_field}: {customer_id if customer_id is not None else '—'}\n"
            f"- {esc.creator_id_field}: {creator_id if creator_id is not None else '—'}\n"
            f"- {esc.creator_company_id_field}: {creator_company_id if creator_company_id is not None else '—'}\n"
            "\n"
            "Если вы это видите — доставка эскалации работает ✅"
        )
        try:
            await notification_service._send_notification_safe(
                destination=action.dest,
                text=text,
                context="escalation.test",
            )
            sent += 1
        except Exception as e:
            failed.append(f"mattermost: channel={action.dest.destination_id} -> {e}")

    lines = [
        "📨 escalation_send_test result",
        f"- destinations: {len(escalations)}",
        f"- sent: {sent}",
        f"- config: v{runtime_config.version} ({runtime_config.source})",
    ]
    if failed:
        lines.append(f"- failed: {len(failed)}")
        lines.append("")
        lines.extend(failed)

    return CommandResponse.success("\n".join(lines))


# ============================================================================
# PHASE 3b: User management commands (user_add, user_remove, admin_add, etc.)
# ============================================================================


def _parse_target_id(raw_text: str, command: str = "") -> Optional[str]:
    """Parse mattermost_user_id from command argument.

    Works correctly with full message text "@bot cmd <id>" by extracting
    the args portion first.
    """
    arg_str = _get_command_arg(raw_text, command) if command else raw_text
    parts = (arg_str or "").split()
    if not parts:
        return None
    return parts[0].strip() or None


async def cmd_user_add(
    request: CommandRequest,
    user_store,  # UserStore instance
) -> CommandResponse:
    """
    /user_add <mattermost_user_id>

    Добавляет пользователя с ролью user.
    """
    target_id = _parse_target_id(request.raw_text, request.command)
    if target_id is None:
        return CommandResponse.error("Формат: /user_add <mattermost_user_id>")

    try:
        await user_store.upsert_user(
            mattermost_user_id=target_id,
            role="user",
        )
        await user_store.log_audit(
            mattermost_user_id=target_id,
            action="U:user_add",
            actor_id=request.user.user_id,
        )
        return CommandResponse.success(f"✅ Пользователь добавлен: {target_id}")
    except Exception as e:
        return CommandResponse.error(f"❌ Ошибка: {e}")


async def cmd_user_remove(
    request: CommandRequest,
    user_store,  # UserStore instance
) -> CommandResponse:
    """
    /user_remove <mattermost_user_id>

    Снимает права пользователя (удаляет запись).
    """
    target_id = _parse_target_id(request.raw_text, request.command)
    if target_id is None:
        return CommandResponse.error("Формат: /user_remove <mattermost_user_id>")

    try:
        await user_store.delete_user(target_id)
        await user_store.log_audit(
            mattermost_user_id=target_id,
            action="D:user_remove",
            actor_id=request.user.user_id,
        )
        return CommandResponse.success(f"✅ Пользователь удалён: {target_id}")
    except Exception as e:
        return CommandResponse.error(f"❌ Ошибка: {e}")


async def cmd_admin_add(
    request: CommandRequest,
    user_store,  # UserStore instance
) -> CommandResponse:
    """
    /admin_add <mattermost_user_id>

    Добавляет пользователя с ролью admin.
    """
    target_id = _parse_target_id(request.raw_text, request.command)
    if target_id is None:
        return CommandResponse.error("Формат: /admin_add <mattermost_user_id>")

    try:
        await user_store.upsert_user(
            mattermost_user_id=target_id,
            role="admin",
        )
        await user_store.log_audit(
            mattermost_user_id=target_id,
            action="U:admin_add",
            actor_id=request.user.user_id,
        )
        return CommandResponse.success(f"✅ Админ добавлен: {target_id}")
    except Exception as e:
        return CommandResponse.error(f"❌ Ошибка: {e}")


async def cmd_user_list(
    request: CommandRequest,
    user_store,  # UserStore instance
) -> CommandResponse:
    """
    /user_list [admins|users] [history]

    Показывает список пользователей и админов.
    """
    parts = _get_command_arg(request.raw_text or "", request.command).split()
    role_filter = None
    show_history = False

    if len(parts) >= 1:
        arg = parts[0].strip().lower()
        if arg in {"admin", "admins"}:
            role_filter = "admin"
        elif arg in {"user", "users"}:
            role_filter = "user"
        if len(parts) >= 2 and parts[1].strip().lower() == "history":
            show_history = True

    try:
        items = await user_store.list_users(limit=200)
        if not items:
            return CommandResponse.success("Список пользователей пуст.")

        if role_filter:
            items = [it for it in items if it.get("role") == role_filter]

        title = "Админы" if role_filter == "admin" else "Пользователи"
        if role_filter is None:
            title = "Пользователи и админы"

        lines = [f"{title} (до 200):"]
        for it in items:
            role = it.get("role", "—")
            mm_id = it.get("mattermost_user_id", "—")
            username = it.get("username") or "—"
            full_name = it.get("full_name") or "—"

            if show_history:
                last_cmd = it.get("last_command") or "—"
                last_at = it.get("last_command_at")
                last_at_s = last_at.strftime("%Y-%m-%d %H:%M:%S") if last_at else "—"
                lines.append(f"- {role:6} {mm_id:26} @{username:20} {full_name:22} {last_cmd} @ {last_at_s}")
            else:
                lines.append(f"- {role:6} {mm_id:26} @{username:20} {full_name:22}")

        return CommandResponse.success("\n".join(lines))
    except Exception as e:
        return CommandResponse.error(f"❌ Ошибка: {e}")


async def cmd_user_history(
    request: CommandRequest,
    user_store,  # UserStore instance
) -> CommandResponse:
    """
    /user_history <mattermost_user_id> [limit]

    Показывает историю команд пользователя.
    """
    parts = _get_command_arg(request.raw_text or "", request.command).split()
    if len(parts) < 1 or not parts[0].strip():
        return CommandResponse.error("Формат: /user_history <mattermost_user_id> [limit]")

    target_id = parts[0].strip()
    limit = 20
    if len(parts) >= 2:
        try:
            limit = max(1, min(int(parts[1]), 200))
        except Exception:
            limit = 20

    try:
        items = await user_store.list_history(target_id, limit=limit)
        if not items:
            return CommandResponse.success("История команд пустая.")

        lines = [f"История команд для {target_id} (до {limit}):"]
        for it in items:
            cmd = it.get("command") or "—"
            ts = it.get("created_at")
            ts_s = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "—"
            lines.append(f"- {ts_s} {cmd}")

        return CommandResponse.success("\n".join(lines))
    except Exception as e:
        return CommandResponse.error(f"❌ Ошибка: {e}")


async def cmd_user_audit(
    request: CommandRequest,
    user_store,  # UserStore instance
) -> CommandResponse:
    """
    /user_audit <mattermost_user_id> [limit]

    Показывает audit-историю по пользователю.
    """
    parts = _get_command_arg(request.raw_text or "", request.command).split()
    if len(parts) < 1 or not parts[0].strip():
        return CommandResponse.error("Формат: /user_audit <mattermost_user_id> [limit]")

    target_id = parts[0].strip()
    limit = 20
    if len(parts) >= 2:
        try:
            limit = max(1, min(int(parts[1]), 200))
        except Exception:
            limit = 20

    try:
        items = await user_store.list_audit(target_id, limit=limit)
        if not items:
            return CommandResponse.success("Audit-история пустая.")

        lines = [f"Audit для {target_id} (до {limit}):"]
        for it in items:
            action = it.get("action") or "—"
            actor = it.get("actor_id")
            actor_s = str(actor) if actor is not None else "—"
            ts = it.get("created_at")
            ts_s = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else "—"
            lines.append(f"- {ts_s} {action} (actor={actor_s})")

        return CommandResponse.success("\n".join(lines))
    except Exception as e:
        return CommandResponse.error(f"❌ Ошибка: {e}")


# ============================================================================
# PHASE 3c: Config/Admin commands (config, config_diff, eventlog, icons)
# ============================================================================


async def cmd_config(
    request: CommandRequest,
    web_client,  # WebClient instance
    config_sync: ConfigSyncService,
    runtime_config: RuntimeConfig,
    config_admin_token: str,
    config_token: str = "",
) -> CommandResponse:
    """
    Управление конфигурацией.

    /config — показать текущий конфиг
    /config ? — справка
    /config check — краткая сводка
    /config reload — перезагрузить конфиг
    /config <json> — обновить конфиг
    """
    import json

    arg_str = _get_command_arg(request.raw_text or "", request.command)

    if arg_str in {"?", "help", "/?", "-h", "--help"} or arg_str.startswith("?"):
        help_text = _get_config_help_text()
        return CommandResponse.success(help_text)

    if arg_str in {"check", "status"}:
        try:
            await config_sync.refresh(force=False)
        except Exception as e:
            return CommandResponse.error(f"❌ Ошибка синхронизации конфига: {e}")
        try:
            summary = _get_config_summary(runtime_config)
        except Exception as e:
            return CommandResponse.error(f"❌ Ошибка чтения конфига: {type(e).__name__}: {e}")
        return CommandResponse.success(summary)

    if arg_str in {"reload", "refresh"}:
        try:
            await config_sync.refresh(force=True)
        except Exception as e:
            return CommandResponse.error(f"❌ Ошибка перезагрузки конфига: {e}")
        try:
            summary = _get_config_summary(runtime_config)
        except Exception as e:
            return CommandResponse.error(f"❌ Конфиг обновлён, но ошибка чтения сводки: {type(e).__name__}: {e}")
        return CommandResponse.success(f"✅ Конфиг перезагружен.\n{summary}")

    if not arg_str:
        try:
            # Для чтения используем CONFIG_TOKEN (read-only), не CONFIG_ADMIN_TOKEN
            token = config_token or ""
            res = await web_client.get_config(token=token)
            if not res.get("ok"):
                err = res.get("error") or "unknown"
                return CommandResponse.error(f"❌ Не удалось получить конфиг\nПричина: {err}")

            payload = res.get("data")
            if not isinstance(payload, dict):
                return CommandResponse.error("❌ Неожиданный формат ответа")

            raw = json.dumps(payload, ensure_ascii=False, indent=2)
            return CommandResponse.success(f"CONFIG\n```json\n{raw}\n```")
        except Exception as e:
            return CommandResponse.error(f"❌ Ошибка: {e}")

    if not config_admin_token:
        return CommandResponse.error("❌ CONFIG_ADMIN_TOKEN не задан")

    try:
        raw = arg_str.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip().startswith("```"):
                raw = "\n".join(lines[1:-1]).strip()

        # Mattermost заменяет прямые кавычки на типографские — нормализуем обратно
        raw = (
            raw
            .replace("\u201c", '"')  # "
            .replace("\u201d", '"')  # "
            .replace("\u2018", "'")  # '
            .replace("\u2019", "'")  # '
        )

        data = json.loads(raw)
    except Exception as e:
        return CommandResponse.error(f"❌ JSON parse error: {e}")

    if not isinstance(data, dict):
        return CommandResponse.error("❌ JSON должен быть объектом ({}).")

    data.pop("version", None)
    data.pop("source", None)

    try:
        res = await web_client.put_config(data=data, admin_token=config_admin_token)
        if not res.get("ok"):
            err = res.get("error") or "unknown"
            detail = res.get("detail") or (res.get("data") or {}).get("detail") or ""
            msg = f"❌ Не удалось обновить конфиг\nПричина: {err}"
            if detail:
                msg += f"\nДеталь: {detail}"
            return CommandResponse.error(msg)

        await config_sync.refresh(force=True)
        return CommandResponse.success(
            "✅ Конфиг обновлён.\n"
            f"- config: v{runtime_config.version} ({runtime_config.source})"
        )
    except Exception as e:
        return CommandResponse.error(f"❌ Ошибка при обновлении конфига: {e}")


async def cmd_config_diff(
    request: CommandRequest,
    web_client,  # WebClient instance
    config_admin_token: str,
) -> CommandResponse:
    """
    /config_diff <from> <to>

    Показывает diff между версиями конфига.
    """
    parts = _get_command_arg(request.raw_text or "", request.command).split()
    if len(parts) < 2:
        return CommandResponse.error("Формат: /config_diff <from> <to>")

    try:
        v_from = int(parts[0])
        v_to = int(parts[1])
    except Exception:
        return CommandResponse.error("Некорректные версии.")

    try:
        res = await web_client.get_config_diff(v_from=v_from, v_to=v_to, admin_token=config_admin_token)
        if not res.get("ok"):
            return CommandResponse.error(f"❌ Не удалось получить diff: {res.get('error')}")

        data = res.get("data", {})
        changes = data.get("changes") or []
        if not changes:
            return CommandResponse.success("Изменений нет.")

        lines = [f"Diff {v_from} -> {v_to} (первые 20):"]
        for ch in changes[:20]:
            path = ch.get("path")
            frm = ch.get("from")
            to = ch.get("to")
            lines.append(f"- {path}: {frm} -> {to}")
        return CommandResponse.success("\n".join(lines))
    except Exception as e:
        return CommandResponse.error(f"❌ Ошибка: {e}")


async def cmd_last_eventlog_id(
    request: CommandRequest,
    state_store,  # StateStore instance
) -> CommandResponse:
    """
    /last_eventlog_id — показать последний eventlog id
    /last_eventlog_id set <id> — установить последний eventlog id
    """
    from bot.services.eventlog_worker import EVENTLOG_STATE_KEY

    parts = _get_command_arg(request.raw_text or "", request.command).split()
    if state_store is None:
        return CommandResponse.error("State store отключен.")

    if len(parts) == 0:
        data = state_store.get_json(EVENTLOG_STATE_KEY) or {}
        last_id = data.get("last_event_id")
        if last_id is None:
            return CommandResponse.success("Последний eventlog id: —")
        else:
            return CommandResponse.success(f"Последний eventlog id: {last_id}")

    if len(parts) >= 2 and parts[0].lower() == "set":
        try:
            new_id = int(parts[1])
        except Exception:
            return CommandResponse.error("Формат: /last_eventlog_id set <id>")
        state_store.set_json(EVENTLOG_STATE_KEY, {"last_event_id": new_id, "updated_at": time.time()})
        return CommandResponse.success(f"✅ last_eventlog_id обновлён: {new_id}")

    return CommandResponse.error("Формат: /last_eventlog_id или /last_eventlog_id set <id>")


async def cmd_eventlog_poll(
    request: CommandRequest,
    state_store,  # StateStore instance
    eventlog_filter_store,
    eventlog_login: str,
    eventlog_password: str,
    eventlog_base_url: str,
    eventlog_start_id: int,
    notify_eventlog,  # notify_eventlog function from NotificationService
) -> CommandResponse:
    """
    /eventlog_poll

    Принудительный одиночный прогон eventlog.
    """
    from bot.services.eventlog_worker import eventlog_poll_once

    try:
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

        return CommandResponse.success("\n".join(lines))
    except Exception as e:
        return CommandResponse.error(f"❌ Ошибка: {e}")


async def cmd_service_icons(
    request: CommandRequest,
    service_icon_store,  # ServiceIconStore instance
) -> CommandResponse:
    """
    Показывает таблицу значков сервисов.
    """
    try:
        items = await service_icon_store.list_all(limit=100)
        if not items:
            return CommandResponse.success("service_icons пустая.")

        lines = ["service_icons:"]
        for it in items:
            name = it.service_name or "—"
            lines.append(
                f"- service_id={it.service_id} code={it.service_code} icon={it.icon} enabled={it.enabled} name={name}"
            )
        return CommandResponse.success("\n".join(lines))
    except Exception as e:
        return CommandResponse.error(f"❌ Ошибка: {e}")


async def cmd_service_icon_add(
    request: CommandRequest,
    service_icon_store,  # ServiceIconStore instance
) -> CommandResponse:
    """
    /service_icon_add <service_id> <service_code> <icon> [service_name]
    """
    parts = _get_command_arg(request.raw_text or "", request.command).split()
    if len(parts) < 3:
        return CommandResponse.error("Формат: /service_icon_add <service_id> <service_code> <icon> [service_name]")

    try:
        service_id = int(parts[0])
    except Exception:
        return CommandResponse.error("Некорректный service_id.")

    service_code = parts[1].strip()
    icon = parts[2].strip()
    service_name = " ".join(parts[3:]).strip()

    if not service_code or not icon:
        return CommandResponse.error("Формат: /service_icon_add <service_id> <service_code> <icon> [service_name]")

    try:
        await service_icon_store.upsert_icon(
            service_id=service_id,
            service_code=service_code,
            icon=icon,
            service_name=service_name,
            enabled=True,
        )
        return CommandResponse.success(f"✅ service_icon сохранён для service_id={service_id}.")
    except Exception as e:
        return CommandResponse.error(f"❌ Ошибка: {e}")


# ============================================================================
# Helper functions for config management
# ============================================================================


def _get_config_help_text() -> str:
    """Get help text for /config command."""
    return (
        "Формат /config:\n"
        "1) /config — показать текущий конфиг\n"
        "2) /config ? — справка\n"
        "3) /config check — показать краткую сводку\n"
        "4) /config reload — принудительно перезагрузить конфиг\n"
        "5) /config <json> — обновить конфиг\n"
        "\n"
        "Обновление полностью заменяет конфиг.\n"
        "Обязательные поля верхнего уровня: routing, escalation.\n"
        "Поля version и source можно оставить — бот их вырежет.\n"
    )


def _get_config_summary(runtime_config: RuntimeConfig) -> str:
    """Get summary of current config."""
    routing = runtime_config.routing
    eventlog = runtime_config.eventlog
    esc = runtime_config.escalation
    lines = [
        "📦 Конфиг (сводка):",
        f"- version: {runtime_config.version} ({runtime_config.source})",
        f"- routing.rules: {len(routing.rules)} (default_dest={'yes' if routing.default_dest else 'no'})",
        f"- eventlog.rules: {len(eventlog.rules)} (default_dest={'yes' if eventlog.default_dest else 'no'})",
        f"- escalation.enabled: {'yes' if esc.enabled else 'no'}",
        f"- escalation.rules: {len(esc.rules)} (after_s={esc.after_s})",
        f"- escalation.mention: {esc.mention}",
    ]
    return "\n".join(lines)


# ============================================================================
# Identity / diagnostic commands
# ============================================================================


async def cmd_whoami(
    request: CommandRequest,
    user_store,  # UserStore instance
) -> CommandResponse:
    """
    /whoami — показать информацию о себе (id, username, имя, роль).
    """
    user_id = request.user.user_id
    username = request.user.username or "—"
    user_info = request.context.get("user_info") or {}
    first_name = user_info.get("first_name", "")
    last_name = user_info.get("last_name", "")
    full_name = f"{first_name} {last_name}".strip() or "—"
    email = user_info.get("email") or "—"
    nickname = user_info.get("nickname") or ""

    try:
        role = await user_store.get_role_by_mm_id(user_id) or "—"
    except Exception:
        role = "—"

    lines = [
        "👤 **whoami**",
        f"- ID: `{user_id}`",
        f"- Username: @{username}",
        f"- Name: {full_name}" + (f" ({nickname})" if nickname else ""),
        f"- Email: {email}",
        f"- Role (bot): {role}",
    ]
    return CommandResponse.success("\n".join(lines))


async def cmd_whereami(
    request: CommandRequest,
    mattermost_bot,  # MattermostBotAdapter instance
) -> CommandResponse:
    """
    /whereami — показать ID и название текущего канала и команды.
    """
    channel_id = request.context.get("channel_id") or "—"

    team_id = "—"
    team_name = "—"
    team_display = "—"
    channel_name = "—"
    channel_display = "—"

    if channel_id != "—" and mattermost_bot is not None:
        ch = await mattermost_bot.get_channel_info(channel_id)
        if ch:
            channel_name = ch.get("name") or "—"
            channel_display = ch.get("display_name") or channel_name
            team_id = ch.get("team_id") or "—"

        if team_id != "—":
            tm = await mattermost_bot.get_team_info(team_id)
            if tm:
                team_name = tm.get("name") or "—"
                team_display = tm.get("display_name") or team_name

    lines = [
        "📍 **whereami**",
        "",
        "**Team:**",
        f"- ID: `{team_id}`",
        f"- Name: {team_name}  ({team_display})",
        "",
        "**Channel:**",
        f"- ID: `{channel_id}`",
        f"- Name: {channel_name}  ({channel_display})",
        "",
        "Используй `channel_id` в `ROUTES_DEFAULT_DESTINATION_ID` или в правилах конфига.",
    ]
    return CommandResponse.success("\n".join(lines))


# ============================================================================
# Mattermost Help Command
# ============================================================================


async def cmd_help_mattermost(
    request: CommandRequest,
) -> CommandResponse:
    """
    Справка по всем доступным командам в Mattermost.
    """
    lines = [
        "🤖 ServiceBot - Доступные команды\n",
        "**👤 Пользовательские команды:**",
        "- `/whoami` - Кто я: ID, username, имя, роль",
        "- `/whereami` - Где я: ID и название текущего канала и team",
        "- `/user_list [admins|users]` - Список пользователей",
        "- `/user_history <id> [limit]` - История команд пользователя",
        "- `/user_audit <id> [limit]` - Audit история",
        "",
        "**⚙️ Админские команды:**",
        "- `/routes_test name=\"...\" service_id=101` - Тест маршрутизации",
        "- `/routes_debug name=\"...\"` - Подробный отладочный маршрутинг",
        "- `/routes_send_test name=\"...\"` - Отправить тестовое уведомление",
        "- `/escalation_send_test name=\"...\"` - Тест эскалации",
        "- `/user_add <id>` - Добавить пользователя",
        "- `/user_remove <id>` - Удалить пользователя",
        "- `/admin_add <id>` - Добавить админа",
        "- `/config [? | check | reload | json]` - Управление конфигом",
        "- `/config_diff <from> <to>` - Diff конфигов",
        "- `/last_eventlog_id [set <id>]` - Последний eventlog ID",
        "- `/eventlog_poll` - Принудительный прогон eventlog",
        "- `/service_icons` - Показать значки сервисов",
        "- `/service_icon_add <id> <code> <icon>` - Добавить значок",
        "",
        "**ℹ️ Справка:**",
        "- `/help_mattermost` - Эта справка",
    ]
    return CommandResponse.success("\n".join(lines))
