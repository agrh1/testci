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

from bot.utils.escalation import EscalationFilter, EscalationManager
from bot.utils.notify_router import Destination, parse_destination, parse_rules
from bot.utils.state_store import StateStore


@dataclass
class RoutingConfig:
    rules: list
    default_dest: Optional[Destination]
    service_id_field: str
    customer_id_field: str


@dataclass
class EscalationConfig:
    enabled: bool
    after_s: int
    dest: Optional[Destination]
    mention: str
    service_id_field: str
    customer_id_field: str
    flt: EscalationFilter


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

        # менеджер эскалации (создаём только если enabled)
        self._esc_manager: Optional[EscalationManager] = None
        self._rebuild_escalation_manager()

    # -----------------------------
    # Env fallback
    # -----------------------------

    def _load_routing_from_env(self) -> RoutingConfig:
        service_id_field = os.getenv("ROUTES_SERVICE_ID_FIELD", "ServiceId").strip() or "ServiceId"
        customer_id_field = os.getenv("ROUTES_CUSTOMER_ID_FIELD", "CustomerId").strip() or "CustomerId"

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
        )

    def _load_escalation_from_env(self, routing: RoutingConfig) -> EscalationConfig:
        enabled = os.getenv("ESCALATION_ENABLED", "0").strip().lower() in ("1", "true", "yes")
        after_s = int(os.getenv("ESCALATION_AFTER_S", "600"))

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
                self._log.error("ESCALATION_FILTER parse error: %s", e)

        return EscalationConfig(
            enabled=enabled,
            after_s=after_s,
            dest=dest,
            mention=mention,
            service_id_field=service_id_field,
            customer_id_field=customer_id_field,
            flt=flt,
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
        if routing_raw is not None and not isinstance(routing_raw, dict):
            self._log.error("config: routing must be dict")
            return False
        if escalation_raw is not None and not isinstance(escalation_raw, dict):
            self._log.error("config: escalation must be dict")
            return False

        # --- routing ---
        try:
            rr = routing_raw or {}
            rules = parse_rules(rr.get("rules", []))
            default_dest = parse_destination(rr.get("default_dest"))
            service_id_field = (rr.get("service_id_field") or "ServiceId").strip() or "ServiceId"
            customer_id_field = (rr.get("customer_id_field") or "CustomerId").strip() or "CustomerId"

            new_routing = RoutingConfig(
                rules=rules,
                default_dest=default_dest,
                service_id_field=service_id_field,
                customer_id_field=customer_id_field,
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

            flt = EscalationFilter()
            jf = er.get("filter")
            if isinstance(jf, dict):
                keywords = tuple(
                    k.strip().lower()
                    for k in jf.get("keywords", [])
                    if isinstance(k, str) and k.strip()
                )
                service_ids = tuple(int(x) for x in jf.get("service_ids", []) if str(x).strip().isdigit())
                customer_ids = tuple(int(x) for x in jf.get("customer_ids", []) if str(x).strip().isdigit())
                flt = EscalationFilter(keywords=keywords, service_ids=service_ids, customer_ids=customer_ids)

            new_escalation = EscalationConfig(
                enabled=enabled,
                after_s=after_s,
                dest=dest,
                mention=mention,
                service_id_field=service_id_field,
                customer_id_field=customer_id_field,
                flt=flt,
            )
        except Exception as e:
            self._log.error("config: escalation parse error: %s", e)
            return False

        # Применяем атомарно: сначала всё распарсили, затем "переключили".
        old = self.version
        self.version = new_version
        self.source = str(data.get("source") or "web")
        self.routing = new_routing
        self.escalation = new_escalation
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
            after_s=self.escalation.after_s,
            service_id_field=self.escalation.service_id_field,
            customer_id_field=self.escalation.customer_id_field,
            flt=self.escalation.flt,
        )

    # -----------------------------
    # Public helpers
    # -----------------------------

    def get_escalations(self, items: list[dict]) -> list[dict]:
        if self._esc_manager is None:
            return []
        return self._esc_manager.process(items)
