# bot/utils/escalation.py


"""
Эскалация "зависших" тикетов (шаг 25) при условии:
"взяли в работу" == StatusId изменился и теперь НЕ 31.

Ключевая идея
-------------
Мы уже получаем open-очередь через /sd/open, а она возвращает только StatusId=31.
Значит:
- если тикет взяли в работу, его StatusId станет !=31 и он перестанет приходить в open items;
- мы увидим, что тикет исчез из очереди, и выкинем его из локального state.

Это самый надёжный вариант, потому что нам не нужно угадывать "AssigneeId/OwnerId/...".

Как работает
------------
1) На каждом успешном polling цикле получаем текущие open items (StatusId=31).
2) Для каждого тикета фиксируем first_seen_at (когда впервые увидели в open).
3) Если тикет пропал из open — удаляем его из state (считаем "взяли/закрыли/перевели").
4) Если тикет всё ещё в open и висит дольше after_s и ещё не эскалирован — эскалируем 1 раз.

Фильтры (что эскалировать)
--------------------------
Можно ограничить:
- keywords по Name
- service_ids по полю service_id_field (обычно "ServiceId")
- customer_ids по полю customer_id_field (обычно "CustomerId")
- creator_ids по полю creator_id_field (обычно "CreatorId")
- creator_company_ids по полю creator_company_id_field (обычно "CreatorCompanyId")

Если фильтр пустой — эскалируем всё, что висит дольше after_s.
При нескольких правилах достаточно совпадения хотя бы одного фильтра.
В каждом правиле after_s может переопределять базовый порог.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from bot.utils.notify_router import Destination, _norm, _to_int
from bot.utils.state_store import StateStore


@dataclass(frozen=True)
class EscalationFilter:
    keywords: tuple[str, ...] = ()
    service_ids: tuple[int, ...] = ()
    customer_ids: tuple[int, ...] = ()
    creator_ids: tuple[int, ...] = ()
    creator_company_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class EscalationRule:
    dest: Destination
    after_s: int = 0
    mention: Optional[str] = None
    flt: EscalationFilter = EscalationFilter()


@dataclass
class EscalationAction:
    dest: Destination
    mention: str
    items: list[dict[str, Any]]


@dataclass(frozen=True)
class EscalationMatch:
    rule: EscalationRule
    item: dict[str, Any]


@dataclass
class EscalationState:
    # id -> unix ts when first seen in open queue
    seen_at: dict[str, float]
    # rule_key -> (id -> unix ts when escalated)
    escalated_at: dict[str, dict[str, float]]


def match_escalation_filter(
    item: dict[str, Any],
    flt: EscalationFilter,
    *,
    service_id_field: str,
    customer_id_field: str,
    creator_id_field: str,
    creator_company_id_field: str,
) -> bool:
    """
    True если тикет подпадает под фильтр эскалации.
    Если фильтр пустой — эскалируем всё.
    """
    if not (
        flt.keywords
        or flt.service_ids
        or flt.customer_ids
        or flt.creator_ids
        or flt.creator_company_ids
    ):
        return True

    ok = False

    if flt.keywords:
        name = item.get("Name")
        if isinstance(name, str):
            n = _norm(name)
            if any(k in n for k in flt.keywords):
                ok = True

    if not ok and flt.service_ids and service_id_field:
        sid = _to_int(item.get(service_id_field))
        if sid is not None and sid in flt.service_ids:
            ok = True

    if not ok and flt.customer_ids and customer_id_field:
        cid = _to_int(item.get(customer_id_field))
        if cid is not None and cid in flt.customer_ids:
            ok = True

    if not ok and flt.creator_ids and creator_id_field:
        cid = _to_int(item.get(creator_id_field))
        if cid is not None and cid in flt.creator_ids:
            ok = True

    if not ok and flt.creator_company_ids and creator_company_id_field:
        ccid = _to_int(item.get(creator_company_id_field))
        if ccid is not None and ccid in flt.creator_company_ids:
            ok = True

    return ok


class EscalationManager:
    def __init__(
        self,
        *,
        store: Optional[StateStore],
        store_key: str,
        service_id_field: str,
        customer_id_field: str,
        creator_id_field: str,
        creator_company_id_field: str,
        rules: Sequence[EscalationRule],
    ) -> None:
        self._store = store
        self._store_key = store_key
        self._service_id_field = service_id_field
        self._customer_id_field = customer_id_field
        self._creator_id_field = creator_id_field
        self._creator_company_id_field = creator_company_id_field
        self._rules = tuple(rules)

        self._state = EscalationState(seen_at={}, escalated_at={})
        self._load()

    def _load(self) -> None:
        if self._store is None:
            return
        data = self._store.get_json(self._store_key) or {}
        seen = data.get("seen_at", {})
        esc = data.get("escalated_at", {})
        if isinstance(seen, dict):
            self._state.seen_at = {str(k): float(v) for k, v in seen.items() if _to_int(k) is not None}
        if isinstance(esc, dict):
            if esc and all(isinstance(v, (int, float)) for v in esc.values()):
                self._state.escalated_at = {
                    "legacy": {str(k): float(v) for k, v in esc.items() if _to_int(k) is not None}
                }
            else:
                out: dict[str, dict[str, float]] = {}
                for rule_key, items in esc.items():
                    if not isinstance(items, dict):
                        continue
                    out[str(rule_key)] = {str(k): float(v) for k, v in items.items() if _to_int(k) is not None}
                self._state.escalated_at = out

    def _save(self) -> None:
        if self._store is None:
            return
        payload = {
            "seen_at": self._state.seen_at,
            "escalated_at": self._state.escalated_at,
        }
        self._store.set_json(self._store_key, payload)

    def _id_of(self, item: dict[str, Any]) -> Optional[int]:
        return _to_int(item.get("Id"))

    def _rule_key(self, rule: EscalationRule, idx: int) -> str:
        dest = rule.dest
        thread = dest.thread_id if dest.thread_id is not None else 0
        flt = rule.flt
        return (
            f"{idx}:{dest.chat_id}:{thread}:{rule.after_s}:"
            f"{rule.mention or ''}:{','.join(flt.keywords)}:"
            f"{','.join(str(x) for x in flt.service_ids)}:"
            f"{','.join(str(x) for x in flt.customer_ids)}:"
            f"{','.join(str(x) for x in flt.creator_ids)}:"
            f"{','.join(str(x) for x in flt.creator_company_ids)}"
        )

    def process(self, items: Sequence[dict[str, Any]]) -> list[EscalationMatch]:
        """
        Обновляет state и возвращает список совпавших правил эскалации.
        """
        now = time.time()

        current_ids: set[str] = set()
        id_to_item: dict[str, dict[str, Any]] = {}

        # фиксируем "первое появление" для всех тикетов, которые сейчас в open
        for it in items:
            tid = self._id_of(it)
            if tid is None or tid <= 0:
                continue
            k = str(tid)
            current_ids.add(k)
            id_to_item[k] = it

            if k not in self._state.seen_at:
                self._state.seen_at[k] = now

        # если тикет пропал из open — считаем, что его взяли/перевели/закрыли -> чистим state
        for k in list(self._state.seen_at.keys()):
            if k not in current_ids:
                self._state.seen_at.pop(k, None)
                self._state.escalated_at.pop(k, None)

        to_escalate: list[EscalationMatch] = []
        legacy = self._state.escalated_at.get("legacy", {})

        # выбираем те, кто "старше порога" и еще не эскалировались по правилу
        for idx, rule in enumerate(self._rules, start=1):
            rule_key = self._rule_key(rule, idx)
            rule_escalated = self._state.escalated_at.get(rule_key, {})
            for k in current_ids:
                if k in rule_escalated or k in legacy:
                    continue
                it = id_to_item.get(k)
                if not it:
                    continue
                if not match_escalation_filter(
                    it,
                    rule.flt,
                    service_id_field=self._service_id_field,
                    customer_id_field=self._customer_id_field,
                    creator_id_field=self._creator_id_field,
                    creator_company_id_field=self._creator_company_id_field,
                ):
                    continue

                seen_at = self._state.seen_at.get(k, now)
                age = now - seen_at
                if age < rule.after_s:
                    continue

                if rule_key not in self._state.escalated_at:
                    self._state.escalated_at[rule_key] = {}
                self._state.escalated_at[rule_key][k] = now
                to_escalate.append(EscalationMatch(rule=rule, item=it))

        self._save()
        return to_escalate
