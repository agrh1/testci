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

Если фильтр пустой — эскалируем всё, что висит дольше after_s.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from bot.utils.notify_router import _norm, _to_int
from bot.utils.state_store import StateStore


@dataclass(frozen=True)
class EscalationFilter:
    keywords: tuple[str, ...] = ()
    service_ids: tuple[int, ...] = ()
    customer_ids: tuple[int, ...] = ()


@dataclass
class EscalationState:
    # id -> unix ts when first seen in open queue
    seen_at: dict[str, float]
    # id -> unix ts when escalated (to avoid repeats)
    escalated_at: dict[str, float]


class EscalationManager:
    def __init__(
        self,
        *,
        store: Optional[StateStore],
        store_key: str,
        after_s: int,
        service_id_field: str,
        customer_id_field: str,
        flt: EscalationFilter,
    ) -> None:
        self._store = store
        self._store_key = store_key
        self._after_s = after_s
        self._service_id_field = service_id_field
        self._customer_id_field = customer_id_field
        self._filter = flt

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
            self._state.escalated_at = {str(k): float(v) for k, v in esc.items() if _to_int(k) is not None}

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

    def _match_item_filter(self, item: dict[str, Any]) -> bool:
        """
        True если тикет подпадает под фильтр эскалации.
        Если фильтр пустой — эскалируем всё.
        """
        f = self._filter
        if not f.keywords and not f.service_ids and not f.customer_ids:
            return True

        ok = False

        if f.keywords:
            name = item.get("Name")
            if isinstance(name, str):
                n = _norm(name)
                if any(k in n for k in f.keywords):
                    ok = True

        if not ok and f.service_ids and self._service_id_field:
            sid = _to_int(item.get(self._service_id_field))
            if sid is not None and sid in f.service_ids:
                ok = True

        if not ok and f.customer_ids and self._customer_id_field:
            cid = _to_int(item.get(self._customer_id_field))
            if cid is not None and cid in f.customer_ids:
                ok = True

        return ok

    def process(self, items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Обновляет state и возвращает список тикетов, которые нужно эскалировать сейчас.
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

        to_escalate: list[dict[str, Any]] = []

        # выбираем те, кто "старше порога" и еще не эскалировались
        for k in current_ids:
            it = id_to_item.get(k)
            if not it:
                continue

            if not self._match_item_filter(it):
                continue

            seen_at = self._state.seen_at.get(k, now)
            age = now - seen_at

            if age >= self._after_s and k not in self._state.escalated_at:
                self._state.escalated_at[k] = now
                to_escalate.append(it)

        self._save()
        return to_escalate
