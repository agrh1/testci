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

    user_id: str  # telegram_id (as str) or mattermost_user_id
    platform: str  # 'telegram' or 'mattermost'
    username: Optional[str] = None
    full_name: Optional[str] = None

    def __post_init__(self):
        if self.platform not in ("telegram", "mattermost"):
            raise ValueError(f"Invalid platform: {self.platform}")


@dataclass(frozen=True)
class MessageResponse:
    """Unified response from handling a message or command."""

    text: str
    reply_to: Optional[str] = None  # Message ID to reply to
    attachments: Optional[list] = None  # Files, images, etc.
    thread_id: Optional[str] = None  # For threaded replies


class MessageAdapter(Protocol):
    """
    Interface for sending messages to users or destinations.

    Implementations: TelegramMessageAdapter, MattermostMessageAdapter
    """

    async def send_message(
        self,
        user: UserIdentity,
        text: str,
        parse_mode: Optional[str] = None,
    ) -> Optional[str]:
        """
        Send a direct message to a user.

        Args:
            user: Target user identity
            text: Message text (markdown or HTML depending on platform)
            parse_mode: Optional format hint ('HTML', 'Markdown', etc.)

        Returns:
            Message ID on success, None on failure
        """
        ...

    async def send_notification(
        self,
        destination_id: str,
        text: str,
        thread_id: Optional[str] = None,
        parse_mode: Optional[str] = None,
    ) -> Optional[str]:
        """
        Send a notification to a channel/chat.

        Args:
            destination_id: Destination identifier (chat_id for TG, channel_id for MM)
            text: Notification text
            thread_id: Optional thread/topic ID for organized conversations
            parse_mode: Optional format hint

        Returns:
            Message ID on success, None on failure
        """
        ...

    async def get_user_info(self, user: UserIdentity) -> Optional[Dict[str, Any]]:
        """
        Retrieve user information from the platform.

        Returns:
            Dictionary with user info (username, full_name, etc.) or None
        """
        ...

    async def is_alive(self) -> bool:
        """
        Check if the adapter can reach the platform.

        Returns:
            True if platform is reachable, False otherwise
        """
        ...


class StateManager(Protocol):
    """
    Interface for managing user command state across platforms.

    Implementations: TelegramStateManager, MattermostStateManager
    """

    async def get_state(self, user: UserIdentity) -> Optional[Dict[str, Any]]:
        """
        Get user's current command state (for multi-step commands).

        Returns:
            State dict or None if no state exists
        """
        ...

    async def set_state(self, user: UserIdentity, state: Dict[str, Any]) -> None:
        """
        Save user's command state.

        Args:
            user: User identity
            state: State dictionary to save
        """
        ...

    async def clear_state(self, user: UserIdentity) -> None:
        """Clear user's state after command completes."""
        ...
