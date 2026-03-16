"""
Mattermost message adapter and state manager.

Integrates with Mattermost REST API for message sending and user management.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from aiohttp import ClientSession

from bot.adapters.base import UserIdentity
from bot.utils.state_store import StateStore


class MattermostMessageAdapter:
    """Send messages via Mattermost using REST API."""

    def __init__(
        self,
        api_url: str,
        bot_token: str,
        session: ClientSession,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize Mattermost adapter.

        Args:
            api_url: Mattermost server URL (e.g., 'https://mattermost.example.com')
            bot_token: Bot personal access token
            session: aiohttp ClientSession for HTTP requests
            logger: Optional logger
        """
        self._api_url = api_url.rstrip("/")
        self._bot_token = bot_token
        self._session = session
        self._logger = logger or logging.getLogger(__name__)
        self._headers = {
            "Authorization": f"Bearer {bot_token}",
            "Content-Type": "application/json",
        }

    async def send_message(
        self,
        user: UserIdentity,
        text: str,
        parse_mode: Optional[str] = None,
    ) -> Optional[str]:
        """
        Send a direct message to a Mattermost user.

        Creates or retrieves a DM channel and posts the message.

        Args:
            user: User identity (must have platform='mattermost')
            text: Message text (supports Markdown)
            parse_mode: Ignored for Mattermost (always Markdown)

        Returns:
            Post ID on success, None on failure
        """
        if user.platform != "mattermost":
            self._logger.warning(f"Cannot send MM message to {user.platform} user")
            return None

        try:
            # Get or create DM channel with this user
            channel = await self._get_or_create_direct_channel(user.user_id)
            if not channel:
                return None

            # Create post in the DM channel
            return await self._create_post(
                channel_id=channel["id"],
                message=text,
            )
        except Exception as e:
            self._logger.warning(f"Failed to send MM message to {user.user_id}: {e}")
            return None

    async def send_notification(
        self,
        destination_id: str,
        text: str,
        thread_id: Optional[str] = None,
        parse_mode: Optional[str] = None,
    ) -> Optional[str]:
        """
        Send a notification to a Mattermost channel.

        In Mattermost:
        - destination_id is the channel_id
        - thread_id is the root_id for threaded replies (threads in channels)

        Args:
            destination_id: Mattermost channel_id
            text: Notification text (Markdown)
            thread_id: Optional parent post ID for threading
            parse_mode: Ignored

        Returns:
            Post ID on success, None on failure
        """
        try:
            return await self._create_post(
                channel_id=destination_id,
                message=text,
                root_id=thread_id,
            )
        except Exception as e:
            self._logger.warning(f"Failed to send MM notification to {destination_id}: {e}")
            return None

    async def get_user_info(self, user: UserIdentity) -> Optional[Dict[str, Any]]:
        """Retrieve user information from Mattermost."""
        if user.platform != "mattermost":
            return None

        try:
            url = f"{self._api_url}/api/v4/users/{user.user_id}"
            async with self._session.get(url, headers=self._headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._logger.debug(f"User info retrieved for {user.user_id}")
                    return {
                        "id": data.get("id"),
                        "username": data.get("username"),
                        "first_name": data.get("first_name"),
                        "last_name": data.get("last_name"),
                        "email": data.get("email"),
                    }
                else:
                    self._logger.warning(f"Failed to get user info: {resp.status}")
                    return None
        except Exception as e:
            self._logger.warning(f"Failed to get MM user info: {e}")
            return None

    async def is_alive(self) -> bool:
        """Check if Mattermost server is reachable."""
        try:
            url = f"{self._api_url}/api/v4/system/ping"
            async with self._session.get(url, timeout=5) as resp:
                return resp.status == 200
        except Exception as e:
            self._logger.warning(f"Mattermost health check failed: {e}")
            return False

    # ========================================================================
    # Internal methods
    # ========================================================================

    async def _get_or_create_direct_channel(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get or create a direct message channel with a user.

        Returns:
            Channel dict with 'id' field, or None on failure
        """
        try:
            # Try to get existing DM with this user
            url = f"{self._api_url}/api/v4/channels/direct"
            body = json.dumps({"user_id": user_id})

            async with self._session.post(url, headers=self._headers, data=body) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    self._logger.debug(f"DM channel {data.get('id')} for user {user_id}")
                    return data
                else:
                    error_text = await resp.text()
                    self._logger.warning(f"Failed to create DM channel: {resp.status} - {error_text}")
                    return None
        except Exception as e:
            self._logger.warning(f"Error creating DM channel: {e}")
            return None

    async def _create_post(
        self,
        channel_id: str,
        message: str,
        root_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Create a post in a channel.

        Args:
            channel_id: Target channel ID
            message: Post text (Markdown)
            root_id: Optional parent post ID for threading

        Returns:
            Post ID on success, None on failure
        """
        try:
            url = f"{self._api_url}/api/v4/posts"
            body = {
                "channel_id": channel_id,
                "message": message,
            }
            if root_id:
                body["root_id"] = root_id

            async with self._session.post(
                url,
                headers=self._headers,
                data=json.dumps(body),
            ) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    post_id = data.get("id")
                    self._logger.debug(f"Post created in channel {channel_id}: {post_id}")
                    return post_id
                else:
                    error_text = await resp.text()
                    self._logger.warning(f"Failed to create post: {resp.status} - {error_text}")
                    return None
        except Exception as e:
            self._logger.warning(f"Error creating post: {e}")
            return None


class MattermostStateManager:
    """Manage command state for Mattermost users (via Redis or memory)."""

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

        State key: f"mm_state:{user.user_id}"
        """
        if user.platform != "mattermost":
            return None

        key = f"mm_state:{user.user_id}"
        try:
            state = await self._store.get(key)
            if state:
                self._logger.debug(f"State retrieved for MM user {user.user_id}")
            return state
        except Exception as e:
            self._logger.warning(f"Failed to get state for {user.user_id}: {e}")
            return None

    async def set_state(self, user: UserIdentity, state: Dict[str, Any]) -> None:
        """Set user's command state."""
        if user.platform != "mattermost":
            return

        key = f"mm_state:{user.user_id}"
        try:
            # State expires after 1 hour (3600 seconds)
            await self._store.set(key, state, ttl=3600)
            self._logger.debug(f"State saved for MM user {user.user_id}")
        except Exception as e:
            self._logger.warning(f"Failed to set state for {user.user_id}: {e}")

    async def clear_state(self, user: UserIdentity) -> None:
        """Clear user's command state."""
        if user.platform != "mattermost":
            return

        key = f"mm_state:{user.user_id}"
        try:
            await self._store.delete(key)
            self._logger.debug(f"State cleared for MM user {user.user_id}")
        except Exception as e:
            self._logger.warning(f"Failed to clear state for {user.user_id}: {e}")
