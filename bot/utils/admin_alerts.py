"""
Алерты для админов/дежурных.

Содержит:
- парсинг destination из env (Mattermost channel);
- сборку текста алерта.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AdminAlertDestination:
    """
    Куда слать алерты админам/дежурным (Mattermost channel).

    channel_id — обязательный (Mattermost channel ID)
    thread_id — опциональный (root_id для треда)
    """
    channel_id: str
    thread_id: Optional[str] = None


def parse_admin_alert_dest_from_env() -> Optional[AdminAlertDestination]:
    """
    Читает destination из env.

    Приоритет:
    1) ADMIN_ALERT_CHANNEL_ID / ADMIN_ALERT_THREAD_ID
    2) ALERT_CHANNEL_ID / ALERT_THREAD_ID (fallback)
    """
    channel_id = os.getenv("ADMIN_ALERT_CHANNEL_ID", "").strip()
    if channel_id:
        thread_id = os.getenv("ADMIN_ALERT_THREAD_ID", "").strip() or None
        return AdminAlertDestination(channel_id=channel_id, thread_id=thread_id)

    channel_id = os.getenv("ALERT_CHANNEL_ID", "").strip()
    if channel_id:
        thread_id = os.getenv("ALERT_THREAD_ID", "").strip() or None
        return AdminAlertDestination(channel_id=channel_id, thread_id=thread_id)

    return None


def fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def build_no_destination_alert_text(
    *,
    ticket: Optional[dict],
    rules_count: int,
    default_dest_present: bool,
    service_id_field: str,
    customer_id_field: str,
    config_version: Optional[int] = None,
    config_source: Optional[str] = None,
) -> str:
    tid = ticket.get("Id") if isinstance(ticket, dict) else None
    name = ticket.get("Name") if isinstance(ticket, dict) else None
    sid = ticket.get(service_id_field) if isinstance(ticket, dict) else None
    cid = ticket.get(customer_id_field) if isinstance(ticket, dict) else None

    lines = [
        "⚠️ Ticket without destination",
        "",
        "Ticket:",
        f"- id: {tid if tid is not None else '—'}",
        f"- name: {name if name is not None else '—'}",
        f"- {service_id_field}: {sid if sid is not None else '—'}",
        f"- {customer_id_field}: {cid if cid is not None else '—'}",
        "",
        "Routing:",
        f"- rules_count: {rules_count}",
        f"- default_dest_present: {'yes' if default_dest_present else 'no'}",
    ]

    if config_version is not None:
        lines.append(f"- config_version: {config_version}")
    if config_source:
        lines.append(f"- config_source: {config_source}")

    lines += [
        "",
        "Action: проверь routing-конфиг (rules/default_dest).",
    ]
    return "\n".join(lines)


def build_web_degraded_alert_text(
    *,
    health_ok: bool,
    ready_ok: bool,
    health_status: object,
    ready_status: object,
    health_error: Optional[str],
    ready_error: Optional[str],
    attempts: int,
) -> str:
    lines = [
        "⚠️ Web деградировал",
        "",
        f"- health: {'ok' if health_ok else 'fail'} (status={health_status})",
        f"- ready: {'ok' if ready_ok else 'fail'} (status={ready_status})",
        f"- health_error: {health_error or '—'}",
        f"- ready_error: {ready_error or '—'}",
        f"- attempts: {attempts}",
        "",
        "Action: проверь web /health и /ready.",
    ]
    return "\n".join(lines)


def build_redis_degraded_alert_text(*, error: str, last_ok_ts: Optional[float]) -> str:
    lines = [
        "⚠️ Redis деградировал",
        "",
        f"- last_ok: {fmt_ts(last_ok_ts)}",
        f"- error: {error or '—'}",
        "",
        "Action: проверь Redis и сеть.",
    ]
    return "\n".join(lines)


def build_forbidden_send_alert_text(
    *,
    channel_id: str,
    thread_id: Optional[str],
    error: str,
    context: Optional[str] = None,
) -> str:
    lines = [
        "⚠️ Send forbidden",
        "",
        f"- channel_id: {channel_id}",
        f"- thread_id: {thread_id if thread_id is not None else '—'}",
        f"- error: {error}",
    ]
    if context:
        lines.append(f"- context: {context}")
    lines += [
        "",
        "Action: проверь права бота на отправку в канал.",
    ]
    return "\n".join(lines)


def build_rollbacks_alert_text(*, count: int, window_s: int, last_at: Optional[str]) -> str:
    lines = [
        "⚠️ Частые rollback конфигурации",
        "",
        f"- window_s: {window_s}",
        f"- count: {count}",
        f"- last_rollback_at: {last_at or '—'}",
        "",
        "Action: проверь /config/history и причины откатов.",
    ]
    return "\n".join(lines)
