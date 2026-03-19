"""
Unit-тесты валидации конфигурации web (Mattermost-формат).
"""

from __future__ import annotations

import pytest

from web.config_validation import ConfigValidationError, validate_config

MM_DEST = {"platform": "mattermost", "destination_id": "abc123channel"}
MM_DEST_THREAD = {"platform": "mattermost", "destination_id": "abc123channel", "thread_id": "post456"}


def test_validate_config_ok() -> None:
    cfg = {
        "routing": {
            "rules": [
                {"dest": MM_DEST, "enabled": True},
            ],
            "default_dest": MM_DEST,
        },
        "escalation": {"enabled": False},
    }
    validate_config(cfg)


def test_validate_config_ok_no_default_dest() -> None:
    """default_dest может отсутствовать или быть null."""
    cfg = {
        "routing": {"rules": [], "default_dest": None},
        "escalation": {"enabled": False},
    }
    validate_config(cfg)


def test_validate_config_ok_no_rules() -> None:
    cfg = {
        "routing": {"rules": [], "default_dest": MM_DEST},
        "escalation": {"enabled": False},
    }
    validate_config(cfg)


def test_validate_config_ok_with_thread() -> None:
    cfg = {
        "routing": {"rules": [], "default_dest": MM_DEST_THREAD},
        "escalation": {"enabled": False},
    }
    validate_config(cfg)


def test_validate_config_missing_fields() -> None:
    with pytest.raises(ConfigValidationError):
        validate_config({})


def test_validate_config_missing_routing() -> None:
    with pytest.raises(ConfigValidationError):
        validate_config({"escalation": {"enabled": False}})


def test_validate_config_missing_escalation() -> None:
    with pytest.raises(ConfigValidationError):
        validate_config({"routing": {"rules": [], "default_dest": MM_DEST}})


def test_validate_config_invalid_dest_empty_destination_id() -> None:
    """destination_id не может быть пустым."""
    cfg = {
        "routing": {
            "rules": [],
            "default_dest": {"platform": "mattermost", "destination_id": ""},
        },
        "escalation": {"enabled": False},
    }
    with pytest.raises(ConfigValidationError):
        validate_config(cfg)


def test_validate_config_invalid_dest_wrong_platform() -> None:
    cfg = {
        "routing": {
            "rules": [],
            "default_dest": {"platform": "telegram", "destination_id": "abc"},
        },
        "escalation": {"enabled": False},
    }
    with pytest.raises(ConfigValidationError):
        validate_config(cfg)


def test_validate_config_invalid_dest_not_dict() -> None:
    cfg = {
        "routing": {"rules": [], "default_dest": "not-a-dict"},
        "escalation": {"enabled": False},
    }
    with pytest.raises(ConfigValidationError):
        validate_config(cfg)


def test_validate_config_escalation_rules() -> None:
    cfg = {
        "routing": {"rules": [], "default_dest": MM_DEST},
        "escalation": {
            "enabled": True,
            "after_s": 300,
            "mention": "@duty",
            "rules": [
                {"dest": MM_DEST, "after_s": 120, "keywords": ["vip"]},
                {"dest": MM_DEST_THREAD, "service_ids": [101]},
            ],
        },
    }
    validate_config(cfg)


def test_validate_config_escalation_enabled_requires_after_s() -> None:
    cfg = {
        "routing": {"rules": [], "default_dest": MM_DEST},
        "escalation": {"enabled": True},
    }
    with pytest.raises(ConfigValidationError):
        validate_config(cfg)


def test_validate_config_with_eventlog() -> None:
    cfg = {
        "routing": {"rules": [], "default_dest": MM_DEST},
        "escalation": {"enabled": False},
        "eventlog": {"rules": [], "default_dest": MM_DEST},
    }
    validate_config(cfg)


def test_validate_config_eventlog_null_dest() -> None:
    cfg = {
        "routing": {"rules": [], "default_dest": MM_DEST},
        "escalation": {"enabled": False},
        "eventlog": {"rules": [], "default_dest": None},
    }
    validate_config(cfg)
