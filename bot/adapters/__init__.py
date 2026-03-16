"""
Platform adapters for unified message sending and command handling.

Supports:
- Telegram (via aiogram Bot)
- Mattermost (via REST API + WebSocket)
"""

from bot.adapters.base import MessageAdapter, MessageResponse, StateManager, UserIdentity
from bot.adapters.mattermost import MattermostMessageAdapter, MattermostStateManager
from bot.adapters.telegram import TelegramMessageAdapter, TelegramStateManager

__all__ = [
    "MessageAdapter",
    "StateManager",
    "UserIdentity",
    "MessageResponse",
    "TelegramMessageAdapter",
    "TelegramStateManager",
    "MattermostMessageAdapter",
    "MattermostStateManager",
]
