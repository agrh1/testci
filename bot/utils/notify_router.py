# bot/utils/notify_router.py


"""
Маршрутизация уведомлений (шаг 25) с поддержкой thread_id и debug-объяснением.

Зачем нужен этот модуль
-----------------------
- Отправлять уведомления не только в разные чаты, но и в разные thread'ы (темы) внутри чата.
- Гибко маршрутизировать по признакам заявок.
- Давать понятное объяснение, какое правило сработало и почему (для /routes_debug).

MVP критерии совпадения
-----------------------
Правило считается "сработавшим", если совпал ХОТЯ БЫ ОДИН критерий:
- keywords: подстрока в Name (case-insensitive)
- service_ids: item[service_id_field] попадает в список
- customer_ids: item[customer_id_field] попадает в список

Если ни одно правило не сработало — используем default destination.

Формат ROUTES_RULES (JSON):
[
  {
    "dest": {"chat_id": -100111, "thread_id": 10},
    "keywords": ["VIP", "P1"],
    "service_ids": [101, 102],
    "customer_ids": [5001]
  }
]
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence


@dataclass(frozen=True)
class Destination:
    """Куда отправлять сообщение в Telegram."""
    chat_id: int
    thread_id: Optional[int] = None


@dataclass(frozen=True)
class RouteRule:
    """Одно правило маршрутизации."""
    dest: Destination
    keywords: tuple[str, ...] = ()
    service_ids: tuple[int, ...] = ()
    customer_ids: tuple[int, ...] = ()


def _norm(s: str) -> str:
    return s.strip().lower()


def _to_int(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None


def parse_destination(raw: Any) -> Optional[Destination]:
    """
    raw ожидается dict: {"chat_id": ..., "thread_id": ...?}
    thread_id:
      - None / отсутствует => основная лента чата (без треда)
      - >0 => конкретный thread
      - 0 => НЕ используем (невалидная "заглушка")
    """
    if not isinstance(raw, dict):
        return None

    chat_id = _to_int(raw.get("chat_id"))
    if chat_id is None:
        return None

    thread_id = _to_int(raw.get("thread_id"))
    if thread_id == 0:
        # считаем 0 невалидным; лучше убрать поле совсем
        thread_id = None

    return Destination(chat_id=chat_id, thread_id=thread_id)


def parse_rules(raw: Any) -> list[RouteRule]:
    """
    Преобразуем JSON (list[dict]) в RouteRule.
    Некорректные элементы пропускаем, чтобы бот не падал из-за конфига.
    """
    if not isinstance(raw, list):
        return []

    rules: list[RouteRule] = []
    for x in raw:
        if not isinstance(x, dict):
            continue

        dest = parse_destination(x.get("dest"))
        if dest is None:
            continue

        keywords_raw = x.get("keywords", [])
        keywords: tuple[str, ...] = tuple(_norm(k) for k in keywords_raw if isinstance(k, str) and _norm(k))

        service_ids_raw = x.get("service_ids", [])
        service_ids: tuple[int, ...] = tuple(v for v in (_to_int(i) for i in service_ids_raw) if v is not None)

        customer_ids_raw = x.get("customer_ids", [])
        customer_ids: tuple[int, ...] = tuple(v for v in (_to_int(i) for i in customer_ids_raw) if v is not None)

        # Правило без критериев не разрешаем — иначе оно "матчит всё" и ломает смысл default.
        if not keywords and not service_ids and not customer_ids:
            continue

        rules.append(RouteRule(dest=dest, keywords=keywords, service_ids=service_ids, customer_ids=customer_ids))

    return rules


def _collect_names(items: Sequence[dict]) -> list[str]:
    names: list[str] = []
    for it in items:
        n = it.get("Name")
        if isinstance(n, str) and n.strip():
            names.append(_norm(n))
    return names


def _collect_int_field(items: Sequence[dict], field_name: str) -> set[int]:
    values: set[int] = set()
    if not field_name:
        return values
    for it in items:
        v = _to_int(it.get(field_name))
        if v is not None:
            values.add(v)
    return values


def _rule_match_reason(
    *,
    rule: RouteRule,
    names: list[str],
    service_ids_in_items: set[int],
    customer_ids_in_items: set[int],
) -> Optional[str]:
    """
    Возвращает текст причины совпадения или None, если правило не совпало.
    """
    if rule.keywords and names:
        for n in names:
            for k in rule.keywords:
                if k in n:
                    return f"keyword '{k}' in Name"

    if rule.service_ids and service_ids_in_items:
        for sid in rule.service_ids:
            if sid in service_ids_in_items:
                return f"service_id {sid} matched"

    if rule.customer_ids and customer_ids_in_items:
        for cid in rule.customer_ids:
            if cid in customer_ids_in_items:
                return f"customer_id {cid} matched"

    return None


def match_destinations(
    *,
    items: Sequence[dict],
    rules: Sequence[RouteRule],
    service_id_field: str,
    customer_id_field: str,
) -> set[Destination]:
    """
    Возвращает множество destinations (чат+тред), которые должны получить уведомление.
    """
    matched: set[Destination] = set()
    if not items or not rules:
        return matched

    names = _collect_names(items)
    service_ids_in_items = _collect_int_field(items, service_id_field)
    customer_ids_in_items = _collect_int_field(items, customer_id_field)

    for r in rules:
        reason = _rule_match_reason(
            rule=r,
            names=names,
            service_ids_in_items=service_ids_in_items,
            customer_ids_in_items=customer_ids_in_items,
        )
        if reason is not None:
            matched.add(r.dest)

    return matched


def explain_matches(
    *,
    items: Sequence[dict],
    rules: Sequence[RouteRule],
    service_id_field: str,
    customer_id_field: str,
) -> list[dict[str, Any]]:
    """
    Для debug: список по каждому правилу: совпало/нет, и причина (если совпало).
    """
    names = _collect_names(items)
    service_ids_in_items = _collect_int_field(items, service_id_field)
    customer_ids_in_items = _collect_int_field(items, customer_id_field)

    out: list[dict[str, Any]] = []
    for idx, r in enumerate(rules, start=1):
        reason = _rule_match_reason(
            rule=r,
            names=names,
            service_ids_in_items=service_ids_in_items,
            customer_ids_in_items=customer_ids_in_items,
        )
        out.append(
            {
                "index": idx,
                "dest": {"chat_id": r.dest.chat_id, "thread_id": r.dest.thread_id},
                "matched": reason is not None,
                "reason": reason,
                "criteria": {
                    "keywords": list(r.keywords),
                    "service_ids": list(r.service_ids),
                    "customer_ids": list(r.customer_ids),
                },
            }
        )
    return out


def pick_destinations(
    *,
    items: Sequence[dict],
    rules: Sequence[RouteRule],
    default_dest: Optional[Destination],
    service_id_field: str,
    customer_id_field: str,
) -> list[Destination]:
    """
    Итоговый список destinations:
    - если сработали правила: возвращаем их (стабильно отсортировано)
    - если нет: default_dest (если задан)
    """
    matched = match_destinations(
        items=items,
        rules=rules,
        service_id_field=service_id_field,
        customer_id_field=customer_id_field,
    )
    if matched:
        return sorted(matched, key=lambda d: (d.chat_id, d.thread_id or 0))

    if default_dest is None:
        return []
    return [default_dest]
