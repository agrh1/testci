# Destination Platform Parameter Fix - Complete Summary

## Problem

When starting the bot server, the following error occurred:
```
TypeError: Destination.__init__() missing 1 required positional argument: 'platform'
```

This happened in `bot/utils/runtime_config.py` at line 134 during `RuntimeConfig` initialization.

## Root Cause

The `Destination` class was modified to require a `platform` parameter (to support both Telegram and Mattermost), but two helper functions in `runtime_config.py` were still creating `Destination` objects without specifying this parameter:

1. **Line 134** in `_load_routing_from_env()`:
   ```python
   return Destination(chat_id=chat_id, thread_id=thread_id)  # Missing platform!
   ```

2. **Line 310** in `_load_eventlog_from_env()`:
   ```python
   return Destination(chat_id=chat_id, thread_id=thread_id)  # Missing platform!
   ```

## Solution

Fixed both occurrences by adding `platform="telegram"` parameter:

### File: bot/utils/runtime_config.py

**Change 1 - Line 134** (in `_load_routing_from_env`):
```python
# BEFORE:
return Destination(chat_id=chat_id, thread_id=thread_id)

# AFTER:
return Destination(platform="telegram", chat_id=chat_id, thread_id=thread_id)
```

**Change 2 - Line 310** (in `_load_eventlog_from_env`):
```python
# BEFORE:
return Destination(chat_id=chat_id, thread_id=thread_id)

# AFTER:
return Destination(platform="telegram", chat_id=chat_id, thread_id=thread_id)
```

## Why "telegram"?

These environment variable loaders create Destination objects from:
- `ROUTES_DEFAULT_CHAT_ID` / `ROUTES_DEFAULT_THREAD_ID`
- `EVENTLOG_DEFAULT_CHAT_ID` / `EVENTLOG_DEFAULT_THREAD_ID`
- And similar environment variables

Since they are loading Telegram chat IDs from environment variables, they create Telegram destinations. The platform is "telegram".

## Multi-Platform Support Architecture

The fix maintains full multi-platform support:

### Telegram Destination (from env variables)
```python
Destination(
    platform="telegram",
    chat_id=-100111,
    thread_id=10,
)
```

### Mattermost Destination (from JSON config)
```python
Destination(
    platform="mattermost",
    destination_id="channel_id",
    thread_id=None,
)
```

### Backward Compatibility
The `parse_destination()` function in `notify_router.py` handles backward compatibility:
- If no `platform` field is provided, it defaults to `"telegram"`
- This ensures old config formats still work without migration

## Testing

Created comprehensive test suite (`test_destination_platform_fix.py`) with 11 tests:
- ✓ Destination requires platform parameter
- ✓ Telegram Destination creation
- ✓ Mattermost Destination creation
- ✓ Parse Telegram destinations from JSON
- ✓ Parse Mattermost destinations from JSON
- ✓ Backward compatibility (no platform field)
- ✓ Invalid destination handling
- ✓ Mixed platform rules parsing
- ✓ Environment variable based creation
- ✓ Destination immutability (frozen dataclass)
- ✓ Default values handling

**All 11 tests passed successfully.**

## Files Modified

1. **bot/utils/runtime_config.py**
   - Fixed 2 Destination creation statements
   - Both now include `platform="telegram"` parameter

## Files Added

1. **tests/test_destination_platform_fix.py**
   - New test suite (114 lines)
   - Comprehensive validation of Destination platform parameter handling
   - Tests for both Telegram and Mattermost platforms
   - Edge case and error handling tests

## Verification

✓ Python syntax compilation: All files pass `python3 -m py_compile`
✓ Test suite: All 11 tests pass
✓ No regression: Existing test cases work correctly with the fix
✓ Multi-platform support: Both Telegram and Mattermost destinations work
✓ Backward compatibility: Legacy configs without platform field work

## Environment-Based Configuration

Environment variables for routing still work correctly:

```bash
# Telegram routing destination
ROUTES_DEFAULT_CHAT_ID=-100111
ROUTES_DEFAULT_THREAD_ID=10

# Eventlog destination
EVENTLOG_DEFAULT_CHAT_ID=555
EVENTLOG_DEFAULT_THREAD_ID=

# These are now correctly converted to
# Destination(platform="telegram", chat_id=..., thread_id=...)
```

## Next Steps

The bot should now start successfully. When it loads configuration:
1. Environment variables are read via the fixed `_dest()` functions
2. Each Destination is created with the correct `platform="telegram"` parameter
3. JSON-based config rules can specify `"platform": "mattermost"` for Mattermost destinations
4. Both platforms work simultaneously in dual-mode deployments
