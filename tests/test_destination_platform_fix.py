"""
Test suite for Destination platform parameter.

Verifies that all Destination objects are created correctly for Mattermost-only mode.
"""

from __future__ import annotations

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.utils.notify_router import Destination, parse_destination, parse_rules


def test_destination_has_default_platform() -> None:
    """Test that Destination.platform defaults to 'mattermost'."""
    dest = Destination(destination_id="channel_abc")
    assert dest.platform == "mattermost"


def test_mattermost_destination() -> None:
    """Test creating Mattermost Destination objects."""
    dest = Destination(
        platform="mattermost",
        destination_id="channel_xyz",
        thread_id=None,
    )

    assert dest.platform == "mattermost"
    assert dest.destination_id == "channel_xyz"
    assert dest.chat_id == 0  # legacy field, not used for Mattermost
    assert dest.thread_id is None


def test_parse_destination_mattermost_explicit() -> None:
    """Test parse_destination with explicit Mattermost platform."""
    raw = {
        "platform": "mattermost",
        "destination_id": "channel_123",
        "thread_id": None,
    }
    dest = parse_destination(raw)

    assert dest is not None
    assert dest.platform == "mattermost"
    assert dest.destination_id == "channel_123"
    assert dest.thread_id is None


def test_parse_destination_mattermost_default_platform() -> None:
    """Test parse_destination backward compatibility: no platform field defaults to mattermost."""
    raw = {
        "destination_id": "channel_555",
        "thread_id": None,
    }
    dest = parse_destination(raw)

    assert dest is not None
    assert dest.platform == "mattermost"
    assert dest.destination_id == "channel_555"


def test_parse_destination_telegram_rejected() -> None:
    """Test that parse_destination rejects Telegram destinations."""
    raw = {
        "platform": "telegram",
        "chat_id": -100111,
        "thread_id": 10,
    }
    dest = parse_destination(raw)
    assert dest is None


def test_parse_destination_invalid() -> None:
    """Test parse_destination with invalid input."""
    # Missing destination_id for Mattermost
    assert parse_destination({"platform": "mattermost"}) is None

    # Telegram platform is rejected
    assert parse_destination({"platform": "telegram", "chat_id": 123}) is None

    # Invalid platform
    assert parse_destination({"platform": "invalid", "destination_id": "x"}) is None

    # Not a dict
    assert parse_destination("not a dict") is None
    assert parse_destination(None) is None


def test_parse_rules_mattermost_only() -> None:
    """Test parse_rules with Mattermost-only rules."""
    raw_rules = [
        {
            "name": "MM Alerts",
            "dest": {"platform": "mattermost", "destination_id": "alerts_channel"},
            "service_ids": [102],
        },
        {
            "name": "MM VIP",
            "dest": {"platform": "mattermost", "destination_id": "vip_channel"},
            "keywords": ["VIP"],
        },
    ]

    rules = parse_rules(raw_rules)

    assert len(rules) == 2
    assert rules[0].name == "MM Alerts"
    assert rules[0].dest.platform == "mattermost"
    assert rules[0].dest.destination_id == "alerts_channel"

    assert rules[1].name == "MM VIP"
    assert rules[1].dest.platform == "mattermost"
    assert rules[1].dest.destination_id == "vip_channel"


def test_parse_rules_skips_telegram() -> None:
    """Test that parse_rules skips Telegram destinations silently."""
    raw_rules = [
        {
            "name": "TG VIP (legacy)",
            "dest": {"platform": "telegram", "chat_id": -100111},
            "service_ids": [101],
        },
        {
            "name": "MM Alerts",
            "dest": {"platform": "mattermost", "destination_id": "alerts_channel"},
            "service_ids": [102],
        },
    ]

    rules = parse_rules(raw_rules)

    # Only MM rule is parsed, TG rule silently dropped
    assert len(rules) == 1
    assert rules[0].name == "MM Alerts"
    assert rules[0].dest.platform == "mattermost"


def test_destination_immutability() -> None:
    """Test that Destination objects are immutable (frozen=True)."""
    dest = Destination(platform="mattermost", destination_id="ch123")

    try:
        dest.destination_id = "other"  # type: ignore
        assert False, "Should not be able to modify frozen dataclass"
    except (AttributeError, Exception):
        pass  # Expected


def test_destination_defaults() -> None:
    """Test Destination default values."""
    dest = Destination(platform="mattermost", destination_id="channel")
    assert dest.thread_id is None
    assert dest.chat_id == 0


if __name__ == "__main__":
    test_functions = [
        test_destination_has_default_platform,
        test_mattermost_destination,
        test_parse_destination_mattermost_explicit,
        test_parse_destination_mattermost_default_platform,
        test_parse_destination_telegram_rejected,
        test_parse_destination_invalid,
        test_parse_rules_mattermost_only,
        test_parse_rules_skips_telegram,
        test_destination_immutability,
        test_destination_defaults,
    ]

    passed = 0
    failed = 0

    for test_func in test_functions:
        try:
            test_func()
            print(f"✓ {test_func.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"✗ {test_func.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test_func.__name__} (error): {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"Tests passed: {passed}")
    print(f"Tests failed: {failed}")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)
    else:
        print("✓✓✓ All tests passed!")
        sys.exit(0)
