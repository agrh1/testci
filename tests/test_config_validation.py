"""
Unit-тесты валидации конфигурации web.
"""

from __future__ import annotations

import pytest

from web.config_validation import ConfigValidationError, validate_config


def test_validate_config_ok() -> None:
    cfg = {
        "routing": {
            "rules": [
                {"dest": {"chat_id": 1, "thread_id": None}, "enabled": True},
            ],
            "default_dest": {"chat_id": 2, "thread_id": None},
        },
        "escalation": {"enabled": False},
    }
    validate_config(cfg)


def test_validate_config_missing_fields() -> None:
    with pytest.raises(ConfigValidationError):
        validate_config({})


def test_validate_config_invalid_dest() -> None:
    cfg = {
        "routing": {"rules": [{"dest": {"chat_id": "x"}}], "default_dest": {}},
        "escalation": {"enabled": False},
    }
    with pytest.raises(ConfigValidationError):
        validate_config(cfg)


def test_validate_config_escalation_rules() -> None:
    cfg = {
        "routing": {"rules": [], "default_dest": {"chat_id": 2, "thread_id": None}},
        "escalation": {
            "enabled": True,
            "after_s": 300,
            "mention": "@duty",
            "rules": [
                {"dest": {"chat_id": 10, "thread_id": None}, "after_s": 120, "keywords": ["vip"]},
                {"dest": {"chat_id": 11, "thread_id": 1}, "service_ids": [101]},
            ],
        },
    }
    validate_config(cfg)
