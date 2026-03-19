"""
Platform adapters for unified message sending and command handling.

Supports Mattermost (via REST API + WebSocket).
"""

from bot.adapters.base import MessageAdapter, MessageResponse, StateManager, UserIdentity
from bot.adapters.mattermost import MattermostMessageAdapter, MattermostStateManager

__all__ = [
    "MessageAdapter",
    "StateManager",
    "UserIdentity",
    "MessageResponse",
    "MattermostMessageAdapter",
    "MattermostStateManager",
]
