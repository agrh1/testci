"""
Unit-тесты эскалаций (фильтры и правила).
"""

from __future__ import annotations

from bot.utils.escalation import (
    EscalationFilter,
    EscalationManager,
    EscalationRule,
    match_escalation_filter,
)
from bot.utils.notify_router import Destination


def test_match_escalation_filter_creator_fields() -> None:
    flt = EscalationFilter(creator_ids=(7001,), creator_company_ids=(9001,))
    item = {"Id": 1, "Name": "ticket", "CreatorId": 7001}
    assert match_escalation_filter(
        item,
        flt,
        service_id_field="ServiceId",
        customer_id_field="CustomerId",
        creator_id_field="CreatorId",
        creator_company_id_field="CreatorCompanyId",
    )


def test_escalation_manager_rules_match() -> None:
    rule = EscalationRule(dest=Destination(chat_id=10), after_s=0, flt=EscalationFilter(service_ids=(101,)))
    manager = EscalationManager(
        store=None,
        store_key="test",
        service_id_field="ServiceId",
        customer_id_field="CustomerId",
        creator_id_field="CreatorId",
        creator_company_id_field="CreatorCompanyId",
        rules=[rule],
    )
    items = [{"Id": 123, "Name": "ticket", "ServiceId": 101}]
    out = manager.process(items)
    assert len(out) == 1
