"""
Telegram message adapter and state manager.

Wraps aiogram.Bot for platform-agnostic message sending.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from aiogram import Bot

from bot.adapters.base import UserIdentity
from bot.utils.state_store import StateStore


class TelegramMessageAdapter:
    """Send messages via Telegram using aiogram Bot."""

    def __init__(self, bot: Bot, logger: Optional[logging.Logger] = None):
        """
        Initialize Telegram adapter.

        Args:
            bot: aiogram Bot instance
            logger: Optional logger for debugging
        """
        self._bot = bot
        self._logger = logger or logging.getLogger(__name__)

    async def send_message(
        self,
        user: UserIdentity,
        text: str,
        parse_mode: Optional[str] = None,
    ) -> Optional[str]:
        """
        Send a direct message to a Telegram user.

        Args:
            user: User identity (must have platform='telegram')
            text: Message text (supports HTML markup)
            parse_mode: Parse mode for text ('HTML', 'Markdown', etc.)

        Returns:
            Message ID as string, or None on failure
        """
        if user.platform != "telegram":
            self._logger.warning(f"Cannot send TG message to {user.platform} user")
            return None

        try:
            # user.user_id is the telegram_id as string
            chat_id = int(user.user_id)
            msg = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode or "HTML",
            )
            self._logger.debug(f"Message sent to TG user {chat_id}: msg_id={msg.message_id}")
            return str(msg.message_id)
        except Exception as e:
            self._logger.warning(f"Failed to send TG message to {user.user_id}: {e}")
            return None

    async def send_notification(
        self,
        destination_id: str,
        text: str,
        thread_id: Optional[str] = None,
        parse_mode: Optional[str] = None,
    ) -> Optional[str]:
        """
        Send a notification to a Telegram chat/topic.

        In Telegram:
        - destination_id is the chat_id (can be negative for groups/supergroups)
        - thread_id is the message_thread_id for topics (if the chat has topics enabled)

        Args:
            destination_id: Telegram chat_id
            text: Notification text
            thread_id: Optional topic/thread ID
            parse_mode: Parse mode ('HTML', 'Markdown', etc.)

        Returns:
            Message ID as string, or None on failure
        """
        try:
            chat_id = int(destination_id)
            msg = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                message_thread_id=int(thread_id) if thread_id else None,
                parse_mode=parse_mode or "HTML",
            )
            self._logger.debug(f"Notification sent to TG chat {chat_id}: msg_id={msg.message_id}")
            return str(msg.message_id)
        except Exception as e:
            self._logger.warning(f"Failed to send TG notification to {destination_id}: {e}")
            return None

    async def get_user_info(self, user: UserIdentity) -> Optional[Dict[str, Any]]:
        """
        Get user info from Telegram.

        Note: aiogram doesn't directly support fetching user info by ID.
        This is a placeholder for future implementation.
        """
        if user.platform != "telegram":
            return None
        # TODO: Implement via Telegram Bot API if needed
        return None

    async def is_alive(self) -> bool:
        """Check if bot token is valid by getting bot info."""
        try:
            me = await self._bot.get_me()
            return me is not None
        except Exception as e:
            self._logger.warning(f"Bot health check failed: {e}")
            return False


class TelegramStateManager:
    """Manage command state for Telegram users (via Redis or memory)."""

    def __init__(self, store: StateStore, logger: Optional[logging.Logger] = None):
        """
        Initialize state manager.

        Args:
            store: State storage backend (StateStore with Redis/memory fallback)
            logger: Optional logger
        """
        self._store = store
        self._logger = logger or logging.getLogger(__name__)

    async def get_state(self, user: UserIdentity) -> Optional[Dict[str, Any]]:
        """
        Get user's command state.

        State key: f"tg_state:{user.user_id}"
        """
        if user.platform != "telegram":
            return None

        key = f"tg_state:{user.user_id}"
        try:
            state = await self._store.get(key)
            if state:
                self._logger.debug(f"State retrieved for TG user {user.user_id}")
            return state
        except Exception as e:
            self._logger.warning(f"Failed to get state for {user.user_id}: {e}")
            return None

    async def set_state(self, user: UserIdentity, state: Dict[str, Any]) -> None:
        """Set user's command state."""
        if user.platform != "telegram":
            return

        key = f"tg_state:{user.user_id}"
        try:
            # State expires after 1 hour (3600 seconds)
            await self._store.set(key, state, ttl=3600)
            self._logger.debug(f"State saved for TG user {user.user_id}")
        except Exception as e:
            self._logger.warning(f"Failed to set state for {user.user_id}: {e}")

    async def clear_state(self, user: UserIdentity) -> None:
        """Clear user's command state."""
        if user.platform != "telegram":
            return

        key = f"tg_state:{user.user_id}"
        try:
            await self._store.delete(key)
            self._logger.debug(f"State cleared for TG user {user.user_id}")
        except Exception as e:
            self._logger.warning(f"Failed to clear state for {user.user_id}: {e}")
