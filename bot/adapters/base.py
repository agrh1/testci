"""
Base protocols and classes for platform adapters.

Defines the interface that all platform-specific adapters must implement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


@dataclass(frozen=True)
class UserIdentity:
    """Unified representation of a user across platforms."""

    user_id: str  # mattermost_user_id
    platform: str  # 'mattermost'
    username: Optional[str] = None
    full_name: Optional[str] = None

    def __post_init__(self):
        if self.platform != "mattermost":
            raise ValueError(f"Invalid platform: {self.platform}")


@dataclass(frozen=True)
class MessageResponse:
    """Unified response from handling a message or command."""

    text: str
    reply_to: Optional[str] = None
    attachments: Optional[list] = None
    thread_id: Optional[str] = None


class MessageAdapter(Protocol):
    """Interface for sending messages to users or destinations."""

    async def send_message(
        self,
        user: UserIdentity,
        text: str,
        parse_mode: Optional[str] = None,
    ) -> Optional[str]: ...

    async def send_notification(
        self,
        destination_id: str,
        text: str,
        thread_id: Optional[str] = None,
        parse_mode: Optional[str] = None,
    ) -> Optional[str]: ...

    async def get_user_info(self, user: UserIdentity) -> Optional[Dict[str, Any]]: ...

    async def is_alive(self) -> bool: ...


class StateManager(Protocol):
    """Interface for managing user command state across platforms."""

    async def get_state(self, user: UserIdentity) -> Optional[Dict[str, Any]]: ...

    async def set_state(self, user: UserIdentity, state: Dict[str, Any]) -> None: ...

    async def clear_state(self, user: UserIdentity) -> None: ...
