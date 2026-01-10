# bot/utils/runtime_config.py

"""
Управление runtime-конфигом бота (routing + escalation) с поддержкой обновлений.

Этот модуль специально отделён от bot/bot_app.py, чтобы:
- упростить тестирование и сопровождение;
- держать всю логику парсинга конфига в одном месте;
- безопасно обрабатывать "кривой" конфиг (не ронять бота).

Источник конфига
----------------
- основной: web /config (обычно хранится в Postgres)
- fallback: переменные окружения (старый механизм), чтобы можно было
  накатывать шаг 26 без одновременного "взрыва" продакшена.

Важно
-----
- Любая ошибка парсинга конфига должна приводить к сохранению предыдущей
  рабочей конфигурации.
- Обновление применяется только если version увеличилась.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from bot.utils.escalation import (
    EscalationAction,
    EscalationFilter,
    EscalationManager,
    EscalationMatch,
    EscalationRule,
)
from bot.utils.notify_router import Destination, parse_destination, parse_rules
from bot.utils.state_store import StateStore


@dataclass
class RoutingConfig:
    rules: list
    default_dest: Optional[Destination]
    service_id_field: str
    customer_id_field: str
    creator_id_field: str
    creator_company_id_field: str


@dataclass
class EscalationConfig:
    enabled: bool
    after_s: int
    dest: Optional[Destination]
    mention: str
    rules: list[EscalationRule]
    service_id_field: str
    customer_id_field: str
    creator_id_field: str
    creator_company_id_field: str


@dataclass
class EventlogConfig:
    rules: list
    default_dest: Optional[Destination]
    service_id_field: str
    customer_id_field: str
    creator_id_field: str
    creator_company_id_field: str


class RuntimeConfig:
    """Текущая активная конфигурация бота.

    Экземпляр живёт весь runtime процесса.
    """

    def __init__(
        self,
        *,
        logger: logging.Logger,
        store: Optional[StateStore],
        escalation_store_key: str = "bot:escalation",
    ) -> None:
        self._log = logger
        self._store = store
        self._esc_store_key = escalation_store_key

        # метаданные источника
        self.version: int = 0
        self.source: str = "env"

        # активные настройки
        self.routing = self._load_routing_from_env()
        self.escalation = self._load_escalation_from_env(self.routing)
        self.eventlog = self._load_eventlog_from_env(self.routing)

        # менеджер эскалации (создаём только если enabled)
        self._esc_manager: Optional[EscalationManager] = None
        self._rebuild_escalation_manager()

    # -----------------------------
    # Env fallback
    # -----------------------------

    def _load_routing_from_env(self) -> RoutingConfig:
        service_id_field = os.getenv("ROUTES_SERVICE_ID_FIELD", "ServiceId").strip() or "ServiceId"
        customer_id_field = os.getenv("ROUTES_CUSTOMER_ID_FIELD", "CustomerId").strip() or "CustomerId"
        creator_id_field = os.getenv("ROUTES_CREATOR_ID_FIELD", "CreatorId").strip() or "CreatorId"
        creator_company_id_field = (
            os.getenv("ROUTES_CREATOR_COMPANY_ID_FIELD", "CreatorCompanyId").strip() or "CreatorCompanyId"
        )

        def _to_int(x: str) -> Optional[int]:
            try:
                x = (x or "").strip()
                if not x:
                    return None
                return int(x)
            except Exception:
                return None

        def _dest(prefix: str) -> Optional[Destination]:
            chat_id = _to_int(os.getenv(f"{prefix}_CHAT_ID", ""))
            if chat_id is None:
                return None
            thread_id = _to_int(os.getenv(f"{prefix}_THREAD_ID", ""))
            if thread_id == 0:
                thread_id = None
            return Destination(chat_id=chat_id, thread_id=thread_id)

        default_dest = _dest("ROUTES_DEFAULT") or _dest("ALERT")

        rules_raw = os.getenv("ROUTES_RULES", "").strip()
        rules = []
        if rules_raw:
            try:
                rules = parse_rules(json.loads(rules_raw))
            except Exception as e:
                self._log.error("ROUTES_RULES parse error: %s", e)
                rules = []

        return RoutingConfig(
            rules=rules,
            default_dest=default_dest,
            service_id_field=service_id_field,
            customer_id_field=customer_id_field,
            creator_id_field=creator_id_field,
            creator_company_id_field=creator_company_id_field,
        )

    def _parse_escalation_filter(self, raw: Any) -> EscalationFilter:
        if not isinstance(raw, dict):
            return EscalationFilter()

        def _ids(values: Any) -> tuple[int, ...]:
            out: list[int] = []
            for v in values or []:
                if str(v).strip().isdigit():
                    out.append(int(v))
            return tuple(out)

        keywords = tuple(
            k.strip().lower()
            for k in raw.get("keywords", [])
            if isinstance(k, str) and k.strip()
        )
        return EscalationFilter(
            keywords=keywords,
            service_ids=_ids(raw.get("service_ids")),
            customer_ids=_ids(raw.get("customer_ids")),
            creator_ids=_ids(raw.get("creator_ids")),
            creator_company_ids=_ids(raw.get("creator_company_ids")),
        )

    def _parse_escalation_rules(
        self,
        raw: Any,
        *,
        base_dest: Optional[Destination],
        base_after_s: int,
    ) -> list[EscalationRule]:
        if not isinstance(raw, list):
            return []

        rules: list[EscalationRule] = []
        for idx, item in enumerate(raw, start=1):
            if not isinstance(item, dict):
                continue
            if item.get("enabled") is False:
                continue

            dest = parse_destination(item.get("dest")) or base_dest
            if dest is None:
                self._log.error("config: escalation.rules[%s] dest is required", idx)
                continue

            mention = item.get("mention")
            if isinstance(mention, str):
                mention = mention.strip() or None
            else:
                mention = None

            after_s = base_after_s
            if "after_s" in item:
                try:
                    after_s = int(item.get("after_s"))
                except Exception:
                    self._log.error("config: escalation.rules[%s].after_s must be int", idx)
                    after_s = base_after_s

            flt_raw = item.get("filter") if isinstance(item.get("filter"), dict) else item
            flt = self._parse_escalation_filter(flt_raw)

            rules.append(EscalationRule(dest=dest, after_s=after_s, mention=mention, flt=flt))

        return rules

    def _load_escalation_from_env(self, routing: RoutingConfig) -> EscalationConfig:
        enabled = os.getenv("ESCALATION_ENABLED", "0").strip().lower() in ("1", "true", "yes")
        def _get_int_env(name: str, default: int) -> int:
            raw = os.getenv(name, str(default)).strip()
            try:
                return int(raw)
            except Exception:
                self._log.error("ENV %s must be int, got %r; using default %s", name, raw, default)
                return default

        after_s = _get_int_env("ESCALATION_AFTER_S", 600)

        # destination
        dest = None
        try:
            raw_dest = {
                "chat_id": os.getenv("ESCALATION_DEST_CHAT_ID", "").strip() or None,
                "thread_id": os.getenv("ESCALATION_DEST_THREAD_ID", "").strip() or None,
            }
            dest = parse_destination(raw_dest)
        except Exception:
            dest = None

        mention = os.getenv("ESCALATION_MENTION", "@duty_engineer").strip() or "@duty_engineer"

        service_id_field = os.getenv("ESCALATION_SERVICE_ID_FIELD", routing.service_id_field).strip() or routing.service_id_field
        customer_id_field = os.getenv("ESCALATION_CUSTOMER_ID_FIELD", routing.customer_id_field).strip() or routing.customer_id_field
        creator_id_field = os.getenv("ESCALATION_CREATOR_ID_FIELD", "CreatorId").strip() or "CreatorId"
        creator_company_id_field = (
            os.getenv("ESCALATION_CREATOR_COMPANY_ID_FIELD", "CreatorCompanyId").strip() or "CreatorCompanyId"
        )

        rules: list[EscalationRule] = []
        rules_env = os.getenv("ESCALATION_RULES")
        if rules_env is not None:
            try:
                raw = rules_env.strip()
                payload = json.loads(raw) if raw else []
                rules = self._parse_escalation_rules(payload, base_dest=dest, base_after_s=after_s)
            except Exception as e:
                self._log.error("ESCALATION_RULES parse error: %s", e)
        else:
            raw = os.getenv("ESCALATION_FILTER", "").strip()
            flt = EscalationFilter()
            if raw:
                try:
                    flt = self._parse_escalation_filter(json.loads(raw))
                except Exception as e:
                    self._log.error("ESCALATION_FILTER parse error: %s", e)
            if dest is not None:
                rules = [EscalationRule(dest=dest, after_s=after_s, mention=None, flt=flt)]

        return EscalationConfig(
            enabled=enabled,
            after_s=after_s,
            dest=dest,
            mention=mention,
            rules=rules,
            service_id_field=service_id_field,
            customer_id_field=customer_id_field,
            creator_id_field=creator_id_field,
            creator_company_id_field=creator_company_id_field,
        )

    def _load_eventlog_from_env(self, routing: RoutingConfig) -> EventlogConfig:
        def _to_int(x: str) -> Optional[int]:
            try:
                x = (x or "").strip()
                if not x:
                    return None
                return int(x)
            except Exception:
                return None

        def _dest(prefix: str) -> Optional[Destination]:
            chat_id = _to_int(os.getenv(f"{prefix}_CHAT_ID", ""))
            if chat_id is None:
                return None
            thread_id = _to_int(os.getenv(f"{prefix}_THREAD_ID", ""))
            if thread_id == 0:
                thread_id = None
            return Destination(chat_id=chat_id, thread_id=thread_id)

        default_dest = _dest("EVENTLOG_DEFAULT") or routing.default_dest

        rules_raw = os.getenv("EVENTLOG_RULES", "").strip()
        rules = []
        if rules_raw:
            try:
                rules = parse_rules(json.loads(rules_raw))
            except Exception as e:
                self._log.error("EVENTLOG_RULES parse error: %s", e)
                rules = []

        service_id_field = os.getenv("EVENTLOG_SERVICE_ID_FIELD", routing.service_id_field).strip() or routing.service_id_field
        customer_id_field = os.getenv("EVENTLOG_CUSTOMER_ID_FIELD", routing.customer_id_field).strip() or routing.customer_id_field
        creator_id_field = os.getenv("EVENTLOG_CREATOR_ID_FIELD", routing.creator_id_field).strip() or routing.creator_id_field
        creator_company_id_field = (
            os.getenv("EVENTLOG_CREATOR_COMPANY_ID_FIELD", routing.creator_company_id_field).strip()
            or routing.creator_company_id_field
        )

        return EventlogConfig(
            rules=rules,
            default_dest=default_dest,
            service_id_field=service_id_field,
            customer_id_field=customer_id_field,
            creator_id_field=creator_id_field,
            creator_company_id_field=creator_company_id_field,
        )

    # -----------------------------
    # Apply dynamic config
    # -----------------------------

    def apply_from_web_config(self, data: dict[str, Any]) -> bool:
        """Применяет конфиг, пришедший из web (/config).

        Возвращает True, если конфиг был обновлён (версия изменилась).

        Требования к data:
        - data["version"] должен быть int
        - data["routing"] и data["escalation"] — dict (могут быть пустыми)

        Если конфиг кривой — НЕ применяем, возвращаем False.
        """
        try:
            new_version = int(data.get("version") or 0)
        except Exception:
            self._log.error("config: invalid version field")
            return False

        if new_version <= self.version:
            return False

        routing_raw = data.get("routing")
        escalation_raw = data.get("escalation")
        eventlog_raw = data.get("eventlog")
        if routing_raw is not None and not isinstance(routing_raw, dict):
            self._log.error("config: routing must be dict")
            return False
        if escalation_raw is not None and not isinstance(escalation_raw, dict):
            self._log.error("config: escalation must be dict")
            return False
        if eventlog_raw is not None and not isinstance(eventlog_raw, dict):
            self._log.error("config: eventlog must be dict")
            return False

        # --- routing ---
        try:
            rr = routing_raw or {}
            rules = parse_rules(rr.get("rules", []))
            default_dest = parse_destination(rr.get("default_dest"))
            service_id_field = (rr.get("service_id_field") or "ServiceId").strip() or "ServiceId"
            customer_id_field = (rr.get("customer_id_field") or "CustomerId").strip() or "CustomerId"
            creator_id_field = (rr.get("creator_id_field") or "CreatorId").strip() or "CreatorId"
            creator_company_id_field = (
                (rr.get("creator_company_id_field") or "CreatorCompanyId").strip() or "CreatorCompanyId"
            )

            new_routing = RoutingConfig(
                rules=rules,
                default_dest=default_dest,
                service_id_field=service_id_field,
                customer_id_field=customer_id_field,
                creator_id_field=creator_id_field,
                creator_company_id_field=creator_company_id_field,
            )
        except Exception as e:
            self._log.error("config: routing parse error: %s", e)
            return False

        # --- escalation ---
        try:
            er = escalation_raw or {}
            enabled = bool(er.get("enabled", False))
            after_s = int(er.get("after_s", 600))
            dest = parse_destination(er.get("dest"))
            mention = (er.get("mention") or "@duty_engineer").strip() or "@duty_engineer"
            service_id_field = (er.get("service_id_field") or new_routing.service_id_field).strip() or new_routing.service_id_field
            customer_id_field = (er.get("customer_id_field") or new_routing.customer_id_field).strip() or new_routing.customer_id_field
            creator_id_field = (
                (er.get("creator_id_field") or new_routing.creator_id_field).strip()
                or new_routing.creator_id_field
            )
            creator_company_id_field = (
                (er.get("creator_company_id_field") or new_routing.creator_company_id_field).strip()
                or new_routing.creator_company_id_field
            )

            rules_raw = er.get("rules")
            if rules_raw is not None:
                rules = self._parse_escalation_rules(rules_raw, base_dest=dest, base_after_s=after_s)
            else:
                flt = EscalationFilter()
                jf = er.get("filter")
                if isinstance(jf, dict):
                    flt = self._parse_escalation_filter(jf)
                rules = (
                    [EscalationRule(dest=dest, after_s=after_s, mention=None, flt=flt)]
                    if dest is not None
                    else []
                )

            new_escalation = EscalationConfig(
                enabled=enabled,
                after_s=after_s,
                dest=dest,
                mention=mention,
                rules=rules,
                service_id_field=service_id_field,
                customer_id_field=customer_id_field,
                creator_id_field=creator_id_field,
                creator_company_id_field=creator_company_id_field,
            )
        except Exception as e:
            self._log.error("config: escalation parse error: %s", e)
            return False

        # --- eventlog ---
        try:
            if eventlog_raw is None:
                new_eventlog = EventlogConfig(
                    rules=new_routing.rules,
                    default_dest=new_routing.default_dest,
                    service_id_field=new_routing.service_id_field,
                    customer_id_field=new_routing.customer_id_field,
                    creator_id_field=new_routing.creator_id_field,
                    creator_company_id_field=new_routing.creator_company_id_field,
                )
            else:
                er = eventlog_raw or {}
                rules = parse_rules(er.get("rules", []))
                default_dest = parse_destination(er.get("default_dest"))
                service_id_field = (er.get("service_id_field") or new_routing.service_id_field).strip() or new_routing.service_id_field
                customer_id_field = (er.get("customer_id_field") or new_routing.customer_id_field).strip() or new_routing.customer_id_field
                creator_id_field = (
                    (er.get("creator_id_field") or new_routing.creator_id_field).strip()
                    or new_routing.creator_id_field
                )
                creator_company_id_field = (
                    (er.get("creator_company_id_field") or new_routing.creator_company_id_field).strip()
                    or new_routing.creator_company_id_field
                )
                new_eventlog = EventlogConfig(
                    rules=rules,
                    default_dest=default_dest,
                    service_id_field=service_id_field,
                    customer_id_field=customer_id_field,
                    creator_id_field=creator_id_field,
                    creator_company_id_field=creator_company_id_field,
                )
        except Exception as e:
            self._log.error("config: eventlog parse error: %s", e)
            return False

        # Применяем атомарно: сначала всё распарсили, затем "переключили".
        old = self.version
        self.version = new_version
        self.source = str(data.get("source") or "web")
        self.routing = new_routing
        self.escalation = new_escalation
        self.eventlog = new_eventlog
        self._rebuild_escalation_manager()

        self._log.info(
            "config updated: version %s -> %s (source=%s)",
            old,
            self.version,
            self.source,
        )
        return True

    def _rebuild_escalation_manager(self) -> None:
        """Пересоздаём EscalationManager под текущие настройки.

        Почему пересоздаём:
        - у менеджера после инициализации фиксируются after_s и фильтры;
        - при изменении конфига проще создать новый инстанс.

        State при этом сохраняется (тот же store_key в Redis/Memory).
        """
        if not self.escalation.enabled:
            self._esc_manager = None
            return

        self._esc_manager = EscalationManager(
            store=self._store,
            store_key=self._esc_store_key,
            service_id_field=self.escalation.service_id_field,
            customer_id_field=self.escalation.customer_id_field,
            creator_id_field=self.escalation.creator_id_field,
            creator_company_id_field=self.escalation.creator_company_id_field,
            rules=self.escalation.rules,
        )

    # -----------------------------
    # Public helpers
    # -----------------------------

    def get_escalations(self, items: list[dict]) -> list[EscalationAction]:
        if self._esc_manager is None:
            return []

        matches: list[EscalationMatch] = self._esc_manager.process(items)
        if not matches:
            return []

        actions: dict[tuple[int, Optional[int], str], EscalationAction] = {}
        for match in matches:
            rule = match.rule
            dest = rule.dest or self.escalation.dest
            if dest is None:
                continue

            mention = rule.mention or self.escalation.mention
            key = (dest.chat_id, dest.thread_id, mention)
            action = actions.get(key)
            if action is None:
                action = EscalationAction(dest=dest, mention=mention, items=[])
                actions[key] = action
            action.items.append(match.item)

        return list(actions.values())
