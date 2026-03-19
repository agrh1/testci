"""
Unit-тесты маршрутизации уведомлений.
"""

from __future__ import annotations

from bot.utils.notify_router import (
    Destination,
    explain_matches,
    match_destinations,
    parse_destination,
    parse_rules,
)


def test_parse_destination_zero_thread_id() -> None:
    dest = parse_destination({"destination_id": "ch123", "thread_id": 0})
    assert dest is not None
    assert dest.destination_id == "ch123"
    assert dest.thread_id is None


def test_parse_rules_skips_invalid_and_empty_rules() -> None:
    rules = parse_rules(
        [
            {"dest": {"destination_id": "ch1"}, "keywords": []},  # нет критериев -> пропускаем
            {"dest": {"platform": "mattermost"}},  # невалидный: нет destination_id
            {"dest": {"destination_id": "ch2"}, "keywords": ["VIP"]},  # валидный
        ]
    )
    assert len(rules) == 1
    assert rules[0].dest == Destination(platform="mattermost", destination_id="ch2", thread_id=None)


def test_match_destinations_keywords_and_ids() -> None:
    rules = parse_rules(
        [
            {"dest": {"destination_id": "ch10"}, "keywords": ["vip"]},
            {"dest": {"destination_id": "ch20"}, "service_ids": [101]},
        ]
    )
    items = [{"Name": "VIP ticket", "ServiceId": 101}]
    matched = match_destinations(
        items=items,
        rules=rules,
        service_id_field="ServiceId",
        customer_id_field="CustomerId",
        creator_id_field="CreatorId",
        creator_company_id_field="CreatorCompanyId",
    )
    assert Destination(platform="mattermost", destination_id="ch10", thread_id=None) in matched
    assert Destination(platform="mattermost", destination_id="ch20", thread_id=None) in matched


def test_explain_matches_contains_reason() -> None:
    rules = parse_rules(
        [
            {"dest": {"destination_id": "ch10"}, "keywords": ["vip"]},
        ]
    )
    items = [{"Name": "vip ticket"}]
    out = explain_matches(
        items=items,
        rules=rules,
        service_id_field="ServiceId",
        customer_id_field="CustomerId",
        creator_id_field="CreatorId",
        creator_company_id_field="CreatorCompanyId",
    )
    assert out[0]["matched"] is True
    assert "keyword" in out[0]["reason"]


def test_match_destinations_creator_fields() -> None:
    rules = parse_rules(
        [
            {"dest": {"destination_id": "ch30"}, "creator_ids": [7001]},
            {"dest": {"destination_id": "ch40"}, "creator_company_ids": [9001]},
        ]
    )
    items = [{"Name": "ticket", "CreatorId": 7001, "CreatorCompanyId": 9001}]
    matched = match_destinations(
        items=items,
        rules=rules,
        service_id_field="ServiceId",
        customer_id_field="CustomerId",
        creator_id_field="CreatorId",
        creator_company_id_field="CreatorCompanyId",
    )
    assert Destination(platform="mattermost", destination_id="ch30", thread_id=None) in matched
    assert Destination(platform="mattermost", destination_id="ch40", thread_id=None) in matched
