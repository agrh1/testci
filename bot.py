from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import ErrorEvent, Message

from bot import ping_reply_text
from bot.utils.escalation import EscalationFilter, EscalationManager
from bot.utils.notify_router import Destination, explain_matches, parse_rules, pick_destinations
from bot.utils.polling import PollingState, polling_open_queue_loop
from bot.utils.sd_web_client import SdWebClient
from bot.utils.state_store import MemoryStateStore, RedisStateStore, ResilientStateStore, StateStore
from bot.utils.web_client import WebClient
from bot.utils.web_filters import WebReadyFilter
from bot.utils.web_guard import WebGuard


def _get_env(name: str, default: Optional[str] = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"ENV {name} is required but not set")
    return value if value is not None else ""


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


def _parse_dest_from_env(prefix: str) -> Optional[Destination]:
    chat_id = _to_int(os.getenv(f"{prefix}_CHAT_ID", "").strip())
    if chat_id is None:
        return None
    thread_id = _to_int(os.getenv(f"{prefix}_THREAD_ID", "").strip())
    if thread_id == 0:
        thread_id = None
    return Destination(chat_id=chat_id, thread_id=thread_id)


def _parse_kv_args(text: str) -> dict[str, str]:
    parts = text.split()
    out: dict[str, str] = {}
    for p in parts[1:]:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        out[k.strip().lower()] = v.strip()

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
    it = {"Id": 999999, "Name": name}
    if service_id is not None:
        it[service_id_field] = service_id
    if customer_id is not None:
        it[customer_id_field] = customer_id
    return it


def _load_routing_from_env() -> tuple[list, Optional[Destination], str, str, Optional[str]]:
    service_id_field = os.getenv("ROUTES_SERVICE_ID_FIELD", "ServiceId").strip() or "ServiceId"
    customer_id_field = os.getenv("ROUTES_CUSTOMER_ID_FIELD", "CustomerId").strip() or "CustomerId"
    default_dest = _parse_dest_from_env("ROUTES_DEFAULT") or _parse_dest_from_env("ALERT")

    rules_raw = os.getenv("ROUTES_RULES", "").strip()
    if not rules_raw:
        return [], default_dest, service_id_field, customer_id_field, "ROUTES_RULES is empty"

    try:
        rules = parse_rules(json.loads(rules_raw))
        return rules, default_dest, service_id_field, customer_id_field, None
    except Exception as e:
        return [], default_dest, service_id_field, customer_id_field, f"ROUTES_RULES parse error: {e}"


def _load_escalation_from_env() -> tuple[bool, int, Optional[Destination], str, str, str, EscalationFilter, Optional[str]]:
    """
    Возвращает:
      enabled, after_s, dest, mention, service_id_field, customer_id_field, filter, error
    """
    enabled = os.getenv("ESCALATION_ENABLED", "0").strip() in ("1", "true", "TRUE", "yes", "YES")
    after_s = int(os.getenv("ESCALATION_AFTER_S", "600"))
    dest = _parse_dest_from_env("ESCALATION_DEST")
    mention = os.getenv("ESCALATION_MENTION", "@duty_engineer").strip() or "@duty_engineer"

    # поля для фильтров
    service_id_field = os.getenv("ESCALATION_SERVICE_ID_FIELD", os.getenv("ROUTES_SERVICE_ID_FIELD", "ServiceId")).strip() or "ServiceId"
    customer_id_field = os.getenv("ESCALATION_CUSTOMER_ID_FIELD", os.getenv("ROUTES_CUSTOMER_ID_FIELD", "CustomerId")).strip() or "CustomerId"

    flt = EscalationFilter()
    raw = os.getenv("ESCALATION_FILTER", "").strip()
    if raw:
        try:
            jf = json.loads(raw)
            if isinstance(jf, dict):
                keywords = tuple(
                    k.strip().lower()
                    for k in jf.get("keywords", [])
                    if isinstance(k, str) and k.strip()
                )
                service_ids = tuple(int(x) for x in jf.get("service_ids", []) if str(x).strip().isdigit())
                customer_ids = tuple(int(x) for x in jf.get("customer_ids", []) if str(x).strip().isdigit())
                flt = EscalationFilter(keywords=keywords, service_ids=service_ids, customer_ids=customer_ids)
        except Exception as e:
            return enabled, after_s, dest, mention, service_id_field, customer_id_field, flt, f"ESCALATION_FILTER parse error: {e}"

    return enabled, after_s, dest, mention, service_id_field, customer_id_field, flt, None


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


async def on_error(event: ErrorEvent) -> None:
    logger = logging.getLogger("bot.errors")
    logger.exception("Unhandled exception in update handling: %s", event.exception)


async def cmd_start(message: Message) -> None:
    await message.answer(
        "Команды: /ping /status /needs_web /sd_open /routes_test /routes_debug /routes_send_test /escalation_send_test"
    )


async def cmd_ping(message: Message) -> None:
    await message.answer(ping_reply_text())


async def cmd_status(
    message: Message,
    web_client: WebClient,
    polling_state: PollingState,
    state_store: Optional[StateStore],
) -> None:
    env = _get_env("ENVIRONMENT", "unknown")
    git_sha = _get_env("GIT_SHA", "unknown")
    web_base_url = _get_env("WEB_BASE_URL", "http://web:8000")

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


async def cmd_routes_test(message: Message) -> None:
    args = _parse_kv_args(message.text or "")
    name = args.get("name", "test ticket")
    service_id = _to_int(args.get("service_id", "")) if "service_id" in args else None
    customer_id = _to_int(args.get("customer_id", "")) if "customer_id" in args else None

    rules, default_dest, service_id_field, customer_id_field, err = _load_routing_from_env()
    if err:
        await message.answer(f"❌ {err}")
        return

    fake = _build_fake_item(
        name=name,
        service_id_field=service_id_field,
        customer_id_field=customer_id_field,
        service_id=service_id,
        customer_id=customer_id,
    )
    dests = pick_destinations(
        items=[fake],
        rules=rules,
        default_dest=default_dest,
        service_id_field=service_id_field,
        customer_id_field=customer_id_field,
    )

    lines = [
        "🧪 routes_test",
        f"- Name: {name}",
        f"- {service_id_field}: {service_id if service_id is not None else '—'}",
        f"- {customer_id_field}: {customer_id if customer_id is not None else '—'}",
        f"- rules: {len(rules)}",
        "",
        "Destinations:",
    ]
    if not dests:
        lines.append("— (ничего; default_dest тоже не задан)")
    else:
        for d in dests:
            lines.append(f"- chat_id={d.chat_id}, thread_id={d.thread_id if d.thread_id is not None else '—'}")

    await message.answer("\n".join(lines))


async def cmd_routes_debug(message: Message) -> None:
    args = _parse_kv_args(message.text or "")
    name = args.get("name", "test ticket")
    service_id = _to_int(args.get("service_id", "")) if "service_id" in args else None
    customer_id = _to_int(args.get("customer_id", "")) if "customer_id" in args else None

    rules, _default_dest, service_id_field, customer_id_field, err = _load_routing_from_env()
    if err:
        await message.answer(f"❌ {err}")
        return

    fake = _build_fake_item(
        name=name,
        service_id_field=service_id_field,
        customer_id_field=customer_id_field,
        service_id=service_id,
        customer_id=customer_id,
    )

    debug = explain_matches(
        items=[fake],
        rules=rules,
        service_id_field=service_id_field,
        customer_id_field=customer_id_field,
    )

    lines = [
        "🔎 routes_debug",
        f"- Name: {name}",
        f"- {service_id_field}: {service_id if service_id is not None else '—'}",
        f"- {customer_id_field}: {customer_id if customer_id is not None else '—'}",
        f"- rules: {len(rules)}",
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


async def cmd_routes_send_test(message: Message, bot: Bot) -> None:
    args = _parse_kv_args(message.text or "")
    name = args.get("name", "test ticket")
    service_id = _to_int(args.get("service_id", "")) if "service_id" in args else None
    customer_id = _to_int(args.get("customer_id", "")) if "customer_id" in args else None

    rules, default_dest, service_id_field, customer_id_field, err = _load_routing_from_env()
    if err:
        await message.answer(f"❌ {err}")
        return

    fake = _build_fake_item(
        name=name,
        service_id_field=service_id_field,
        customer_id_field=customer_id_field,
        service_id=service_id,
        customer_id=customer_id,
    )

    dests = pick_destinations(
        items=[fake],
        rules=rules,
        default_dest=default_dest,
        service_id_field=service_id_field,
        customer_id_field=customer_id_field,
    )

    if not dests:
        await message.answer("❌ Destinations пустой (нет default_dest и не сработали правила)")
        return

    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
    text = (
        "🧪 TEST MESSAGE (routes)\n"
        f"Time: {ts}\n"
        f"Name: {name}\n"
        f"{service_id_field}: {service_id if service_id is not None else '—'}\n"
        f"{customer_id_field}: {customer_id if customer_id is not None else '—'}\n"
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


async def cmd_escalation_send_test(message: Message, bot: Bot) -> None:
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

    enabled, after_s, dest, mention, service_id_field, customer_id_field, flt, err = _load_escalation_from_env()
    if err:
        await message.answer(f"❌ {err}")
        return

    if not enabled:
        await message.answer("❌ ESCALATION_ENABLED=0 (включите эскалацию в env)")
        return

    if dest is None:
        await message.answer("❌ ESCALATION_DEST_CHAT_ID не задан (и/или некорректен)")
        return

    fake = _build_fake_item(
        name=name,
        service_id_field=service_id_field,
        customer_id_field=customer_id_field,
        service_id=service_id,
        customer_id=customer_id,
    )

    matched = _match_escalation_filter(fake, flt, service_id_field, customer_id_field)
    if not matched:
        await message.answer(
            "⚠️ Заявка НЕ проходит ESCALATION_FILTER, поэтому тестовое сообщение НЕ отправляю.\n"
            f"Параметры:\n- Name={name}\n- {service_id_field}={service_id}\n- {customer_id_field}={customer_id}\n"
            f"Фильтр: keywords={list(flt.keywords)} service_ids={list(flt.service_ids)} customer_ids={list(flt.customer_ids)}"
        )
        return

    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
    text = (
        "🚨 TEST MESSAGE (escalation)\n"
        f"Time: {ts}\n"
        f"After_s (config): {after_s}\n"
        f"{mention} заберите в работу, пожалуйста.\n"
        "\n"
        f"- #{fake.get('Id')}: {fake.get('Name')}\n"
        f"- {service_id_field}: {service_id if service_id is not None else '—'}\n"
        f"- {customer_id_field}: {customer_id if customer_id is not None else '—'}\n"
        "\n"
        "Если вы это видите — доставка эскалации в ESCALATION_DEST работает ✅"
    )

    try:
        await bot.send_message(chat_id=dest.chat_id, message_thread_id=dest.thread_id, text=text)
        await message.answer(
            "📨 escalation_send_test: отправлено ✅\n"
            f"- dest chat_id={dest.chat_id}, thread_id={dest.thread_id if dest.thread_id is not None else '—'}"
        )
    except Exception as e:
        await message.answer(
            "❌ escalation_send_test: не удалось отправить\n"
            f"- dest chat_id={dest.chat_id}, thread_id={dest.thread_id if dest.thread_id is not None else '—'}\n"
            f"- error: {e}"
        )


def _build_escalation_text(items: list[dict], mention: str) -> str:
    now_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
    lines = [
        f"🚨 Эскалация: заявки не взяты в работу вовремя — {now_s}",
        f"{mention} заберите в работу, пожалуйста.",
        "",
    ]
    for it in items:
        lines.append(f"- #{it.get('Id')}: {it.get('Name')}")
    return "\n".join(lines)


async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("bot")

    token = _get_env("TELEGRAM_BOT_TOKEN", required=True)
    web_base_url = _get_env("WEB_BASE_URL", "http://web:8000").rstrip("/")

    web_client = WebClient(
        base_url=web_base_url,
        timeout_s=float(os.getenv("WEB_TIMEOUT_S", "1.5")),
        cache_ttl_s=float(os.getenv("WEB_CACHE_TTL_S", "3.0")),
    )
    web_guard = WebGuard(web_client)

    sd_web_client = SdWebClient(
        base_url=web_base_url,
        timeout_s=float(os.getenv("SD_WEB_TIMEOUT_S", "3")),
    )

    redis_url = os.getenv("REDIS_URL", "").strip()
    state_store: Optional[StateStore] = None
    if redis_url:
        socket_timeout_s = float(os.getenv("REDIS_SOCKET_TIMEOUT_S", "1.0"))
        socket_connect_timeout_s = float(os.getenv("REDIS_CONNECT_TIMEOUT_S", "1.0"))
        primary = RedisStateStore(
            redis_url,
            prefix="testci",
            socket_timeout_s=socket_timeout_s,
            socket_connect_timeout_s=socket_connect_timeout_s,
        )
        fallback = MemoryStateStore(prefix="testci")
        state_store = ResilientStateStore(primary, fallback)
        with contextlib.suppress(Exception):
            getattr(state_store, "ping", lambda: None)()

    polling_state = PollingState()
    stop_event = asyncio.Event()

    poll_interval_s = float(os.getenv("POLL_INTERVAL_S", "30"))
    poll_max_backoff_s = float(os.getenv("POLL_MAX_BACKOFF_S", "300"))
    min_notify_interval_s = float(os.getenv("MIN_NOTIFY_INTERVAL_S", "60"))
    max_items_in_message = int(os.getenv("MAX_ITEMS_IN_MESSAGE", "10"))

    # routing
    default_dest = _parse_dest_from_env("ROUTES_DEFAULT") or _parse_dest_from_env("ALERT")
    service_id_field = os.getenv("ROUTES_SERVICE_ID_FIELD", "ServiceId").strip() or "ServiceId"
    customer_id_field = os.getenv("ROUTES_CUSTOMER_ID_FIELD", "CustomerId").strip() or "CustomerId"

    rules_raw = os.getenv("ROUTES_RULES", "").strip()
    rules = []
    if rules_raw:
        try:
            rules = parse_rules(json.loads(rules_raw))
        except Exception as e:
            logger.error("ROUTES_RULES parse error: %s", e)
            rules = []

    # escalation
    esc_enabled = os.getenv("ESCALATION_ENABLED", "0").strip() in ("1", "true", "TRUE", "yes", "YES")
    esc_after_s = int(os.getenv("ESCALATION_AFTER_S", "600"))
    esc_dest = _parse_dest_from_env("ESCALATION_DEST")
    esc_mention = os.getenv("ESCALATION_MENTION", "@duty_engineer").strip() or "@duty_engineer"

    esc_service_id_field = os.getenv("ESCALATION_SERVICE_ID_FIELD", service_id_field).strip() or service_id_field
    esc_customer_id_field = os.getenv("ESCALATION_CUSTOMER_ID_FIELD", customer_id_field).strip() or customer_id_field

    esc_filter_raw = os.getenv("ESCALATION_FILTER", "").strip()
    esc_filter = EscalationFilter()
    if esc_filter_raw:
        try:
            jf = json.loads(esc_filter_raw)
            if isinstance(jf, dict):
                keywords = tuple(
                    k.strip().lower()
                    for k in jf.get("keywords", [])
                    if isinstance(k, str) and k.strip()
                )
                service_ids = tuple(int(x) for x in jf.get("service_ids", []) if str(x).strip().isdigit())
                customer_ids = tuple(int(x) for x in jf.get("customer_ids", []) if str(x).strip().isdigit())
                esc_filter = EscalationFilter(keywords=keywords, service_ids=service_ids, customer_ids=customer_ids)
        except Exception as e:
            logger.error("ESCALATION_FILTER parse error: %s", e)

    esc_manager: Optional[EscalationManager] = None
    if esc_enabled:
        esc_manager = EscalationManager(
            store=state_store,
            store_key="bot:escalation",
            after_s=esc_after_s,
            service_id_field=esc_service_id_field,
            customer_id_field=esc_customer_id_field,
            flt=esc_filter,
        )

    bot = Bot(token=token)
    dp = Dispatcher()

    dp.workflow_data["web_client"] = web_client
    dp.workflow_data["web_guard"] = web_guard
    dp.workflow_data["sd_web_client"] = sd_web_client
    dp.workflow_data["polling_state"] = polling_state
    dp.workflow_data["state_store"] = state_store

    dp.errors.register(on_error)

    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_ping, Command("ping"))
    dp.message.register(cmd_status, Command("status"))
    dp.message.register(cmd_sd_open, Command("sd_open"))
    dp.message.register(cmd_needs_web, Command("needs_web"), WebReadyFilter("/needs_web"))

    dp.message.register(cmd_routes_test, Command("routes_test"))
    dp.message.register(cmd_routes_debug, Command("routes_debug"))
    dp.message.register(cmd_routes_send_test, Command("routes_send_test"))
    dp.message.register(cmd_escalation_send_test, Command("escalation_send_test"))

    async def notify_main(items: list[dict], text: str) -> None:
        dests = pick_destinations(
            items=items,
            rules=rules,
            default_dest=default_dest,
            service_id_field=service_id_field,
            customer_id_field=customer_id_field,
        )
        if not dests:
            logging.getLogger("bot.notify").info("No destinations configured for main notify, skip.")
            return
        for d in dests:
            await bot.send_message(chat_id=d.chat_id, message_thread_id=d.thread_id, text=text)

    async def notify_escalation(items: list[dict], _marker: str) -> None:
        if not esc_enabled or esc_dest is None:
            return
        text = _build_escalation_text(items, mention=esc_mention)
        await bot.send_message(chat_id=esc_dest.chat_id, message_thread_id=esc_dest.thread_id, text=text)

    def get_escalations(items: list[dict]) -> list[dict]:
        if esc_manager is None:
            return []
        return esc_manager.process(items)

    polling_task = asyncio.create_task(
        polling_open_queue_loop(
            state=polling_state,
            stop_event=stop_event,
            sd_web_client=sd_web_client,
            notify_main=notify_main,
            notify_escalation=notify_escalation if esc_enabled else None,
            get_escalations=get_escalations if esc_enabled else None,
            base_interval_s=poll_interval_s,
            max_backoff_s=poll_max_backoff_s,
            min_notify_interval_s=min_notify_interval_s,
            max_items_in_message=max_items_in_message,
            store=state_store,
            store_key="bot:open_queue",
        ),
        name="polling_open_queue",
    )

    logger.info("Bot started. WEB_BASE_URL=%s POLL_INTERVAL_S=%s", web_base_url, poll_interval_s)

    try:
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        polling_task.cancel()
        # CancelledError наследуется от BaseException, поэтому Exception его не ловит
        with contextlib.suppress(asyncio.CancelledError):
            await polling_task


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
