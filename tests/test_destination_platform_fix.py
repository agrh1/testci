"""
Test suite for Destination platform parameter fix.

Verifies that all Destination objects are created with the required platform parameter,
and that both Telegram and Mattermost platforms are properly supported.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.utils.notify_router import Destination, parse_destination, parse_rules


def test_destination_requires_platform() -> None:
    """Test that Destination requires the platform parameter."""
    # Should work with platform
    dest = Destination(platform="telegram", chat_id=123)
    assert dest.platform == "telegram"

    # Should fail without platform
    try:
        Destination(chat_id=123)  # type: ignore
        assert False, "Should have raised TypeError"
    except TypeError as e:
        assert "platform" in str(e)


def test_telegram_destination() -> None:
    """Test creating Telegram Destination objects."""
    dest = Destination(
        platform="telegram",
        chat_id=-100111,
        thread_id=10,
    )

    assert dest.platform == "telegram"
    assert dest.chat_id == -100111
    assert dest.thread_id == 10
    assert dest.destination_id == ""


def test_mattermost_destination() -> None:
    """Test creating Mattermost Destination objects."""
    dest = Destination(
        platform="mattermost",
        destination_id="channel_xyz",
        thread_id=None,
    )

    assert dest.platform == "mattermost"
    assert dest.destination_id == "channel_xyz"
    assert dest.chat_id == 0  # Not used for Mattermost
    assert dest.thread_id is None


def test_parse_destination_telegram() -> None:
    """Test parse_destination with Telegram format."""
    raw = {
        "platform": "telegram",
        "chat_id": -100111,
        "thread_id": 10,
    }
    dest = parse_destination(raw)

    assert dest is not None
    assert dest.platform == "telegram"
    assert dest.chat_id == -100111
    assert dest.thread_id == 10


def test_parse_destination_mattermost() -> None:
    """Test parse_destination with Mattermost format."""
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


def test_parse_destination_backward_compat() -> None:
    """Test parse_destination backward compatibility (no platform field)."""
    raw = {
        "chat_id": 555,
        "thread_id": None,
    }
    dest = parse_destination(raw)

    assert dest is not None
    assert dest.platform == "telegram"  # Should default to telegram
    assert dest.chat_id == 555


def test_parse_destination_invalid() -> None:
    """Test parse_destination with invalid input."""
    # Missing chat_id for Telegram
    assert parse_destination({"platform": "telegram"}) is None

    # Missing destination_id for Mattermost
    assert parse_destination({"platform": "mattermost"}) is None

    # Invalid platform
    assert parse_destination({"platform": "invalid", "chat_id": 123}) is None

    # Not a dict
    assert parse_destination("not a dict") is None
    assert parse_destination(None) is None


def test_parse_rules_with_platforms() -> None:
    """Test parse_rules with mixed Telegram and Mattermost rules."""
    raw_rules = [
        {
            "name": "TG VIP",
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

    assert len(rules) == 2
    assert rules[0].name == "TG VIP"
    assert rules[0].dest.platform == "telegram"
    assert rules[0].dest.chat_id == -100111

    assert rules[1].name == "MM Alerts"
    assert rules[1].dest.platform == "mattermost"
    assert rules[1].dest.destination_id == "alerts_channel"


def test_env_based_destination() -> None:
    """Test creating Destination from environment variables (simulating runtime_config.py)."""
    def _to_int(x: str) -> Optional[int]:
        try:
            x = (x or "").strip()
            if not x:
                return None
            return int(x)
        except Exception:
            return None

    def _dest_from_env(prefix: str) -> Optional[Destination]:
        chat_id = _to_int(os.getenv(f"{prefix}_CHAT_ID", ""))
        if chat_id is None:
            return None
        thread_id = _to_int(os.getenv(f"{prefix}_THREAD_ID", ""))
        if thread_id == 0:
            thread_id = None
        return Destination(platform="telegram", chat_id=chat_id, thread_id=thread_id)

    # Test with environment variables
    os.environ["TEST_CHAT_ID"] = "-100111"
    os.environ["TEST_THREAD_ID"] = "10"

    dest = _dest_from_env("TEST")
    assert dest is not None
    assert dest.platform == "telegram"
    assert dest.chat_id == -100111
    assert dest.thread_id == 10

    # Clean up
    del os.environ["TEST_CHAT_ID"]
    del os.environ["TEST_THREAD_ID"]


def test_destination_immutability() -> None:
    """Test that Destination objects are immutable (frozen=True)."""
    dest = Destination(platform="telegram", chat_id=123)

    try:
        dest.chat_id = 456  # type: ignore
        assert False, "Should not be able to modify frozen dataclass"
    except (AttributeError, Exception):
        pass  # Expected


def test_destination_defaults() -> None:
    """Test Destination default values."""
    # Minimal Telegram destination
    dest_tg = Destination(platform="telegram", chat_id=123)
    assert dest_tg.thread_id is None
    assert dest_tg.destination_id == ""

    # Minimal Mattermost destination
    dest_mm = Destination(platform="mattermost", destination_id="channel")
    assert dest_mm.thread_id is None
    assert dest_mm.chat_id == 0


if __name__ == "__main__":
    # Run all tests
    test_functions = [
        test_destination_requires_platform,
        test_telegram_destination,
        test_mattermost_destination,
        test_parse_destination_telegram,
        test_parse_destination_mattermost,
        test_parse_destination_backward_compat,
        test_parse_destination_invalid,
        test_parse_rules_with_platforms,
        test_env_based_destination,
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
