"""
Mattermost Bot Adapter - интеграция через Bot Account + WebSocket.

Подключается к Mattermost как бот (через WebSocket) и слушает сообщения.
Команды вызываются через @mention или legacy /command.

Возможности:
- Слушать сообщения с командами (@botname command или /command)
- Отправлять сообщения в каналы и в ответ на конкретные посты
- Поддержка threading (root_id для ответов в ветках)
- Автоматическое переподключение при обрыве WebSocket
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

try:
    from mattermostdriver import Driver
except ImportError:
    Driver = None  # type: ignore

from bot.adapters.base import UserIdentity


class MattermostBotAdapter:
    """
    Mattermost Bot интеграция через Bot Account + WebSocket.

    Использует выделенный daemon-поток для WebSocket, чтобы не блокировать
    пул executor'ов asyncio (что приводило к таймаутам Telegram).
    """

    def __init__(
        self,
        *,
        server_url: str,
        bot_token: str,
        logger: logging.Logger,
        on_command: Optional[Callable] = None,
    ):
        if Driver is None:
            raise ImportError("Install mattermostdriver: pip install mattermostdriver")

        self.server_url = server_url
        self.bot_token = bot_token
        self.logger = logger
        self.on_command = on_command

        parsed = urlparse(server_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        scheme = parsed.scheme or "https"

        self.driver = Driver(options={
            "url": host,
            "port": port,
            "scheme": scheme,
            "basepath": "/api/v4",
            "token": bot_token,
            "verify": True,
        })

        self._ws_task: Optional[asyncio.Task] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._is_running = False
        self._bot_user_id: Optional[str] = None
        self._bot_username: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Запустить бота: авторизация + WebSocket listener."""
        if self._is_running:
            self.logger.warning("MattermostBotAdapter is already running")
            return

        self._loop = asyncio.get_running_loop()

        # Авторизация (в отдельном потоке, чтобы не блокировать loop)
        await asyncio.to_thread(self.driver.login)

        me = await asyncio.to_thread(self.driver.users.get_user, 'me')
        self._bot_user_id = me['id']
        self._bot_username = me['username']
        self.logger.info("✓ Mattermost bot connected: %s (id=%s)", self._bot_username, self._bot_user_id)

        self._is_running = True
        self._ws_task = asyncio.create_task(self._ws_supervisor(), name="mm-ws-supervisor")
        self.logger.info("✓ WebSocket listener started")

    async def stop(self) -> None:
        """Остановить бота и закрыть WebSocket."""
        self._is_running = False
        if self._ws_task:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
        self.logger.info("✓ MattermostBotAdapter stopped")

    # ------------------------------------------------------------------
    # WebSocket supervisor (reconnect loop)
    # ------------------------------------------------------------------

    async def _ws_supervisor(self) -> None:
        """
        Управляет WebSocket-подключением с автоматическим реконнектом.

        WebSocket запускается в выделенном daemon-потоке (не в пуле executor),
        чтобы не блокировать asyncio.to_thread() вызовы для Telegram.
        """
        reconnect_delay = 5
        max_delay = 60

        while self._is_running:
            try:
                ws_done = self._loop.create_future()

                def _run_ws():
                    """Запуск WebSocket в выделенном потоке."""
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        self.driver.init_websocket(self._handle_ws_message)
                    except Exception as exc:
                        if not ws_done.done():
                            self._loop.call_soon_threadsafe(ws_done.set_exception, exc)
                    finally:
                        loop.close()
                        if not ws_done.done():
                            self._loop.call_soon_threadsafe(ws_done.set_result, None)

                self._ws_thread = threading.Thread(
                    target=_run_ws,
                    name="mattermost-ws",
                    daemon=True,
                )
                self._ws_thread.start()

                await ws_done
                # Если дошли сюда — WebSocket завершился нормально
                reconnect_delay = 5  # сброс при успехе

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.warning("WebSocket disconnected: %s", e)

            if self._is_running:
                self.logger.info("Reconnecting in %ds...", reconnect_delay)
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_delay)

    # ------------------------------------------------------------------
    # WebSocket message handler (async — вызывается из mattermostdriver)
    # ------------------------------------------------------------------

    async def _handle_ws_message(self, msg) -> None:
        """Обработка WebSocket-сообщения. Парсит команды и вызывает on_command."""
        try:
            if isinstance(msg, str):
                msg = json.loads(msg)

            if msg.get('event') != 'posted':
                return

            data = msg.get('data', {})
            post_str = data.get('post', '')
            if not post_str:
                return

            post = json.loads(post_str)
            user_id = post.get('user_id', '')
            channel_id = post.get('channel_id', '')
            message = post.get('message', '').strip()
            post_id = post.get('id', '')

            if user_id == self._bot_user_id:
                return

            command = self._parse_command(message)
            if not command:
                return

            self.logger.debug("MM command from %s: %s in %s", user_id, command, channel_id)

            if self.on_command and self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self.on_command(
                        command=command,
                        text=message,
                        user_id=user_id,
                        channel_id=channel_id,
                        post_id=post_id,
                    ),
                    self._loop,
                )

        except json.JSONDecodeError as e:
            self.logger.warning("Bad JSON in WS message: %s", e)
        except Exception as e:
            self.logger.error("WS message handling error: %s", e)

    def _parse_command(self, message: str) -> Optional[str]:
        """
        Извлечь команду из сообщения. Поддерживает:
          1) @botname command args...
          2) /command args...  (legacy)
        Возвращает имя команды или None.
        """
        mention = f"@{self._bot_username}" if self._bot_username else None

        if mention and message.lower().startswith(mention.lower()):
            after = message[len(mention):].strip()
            if not after:
                return "help_mattermost"
            return after.split(maxsplit=1)[0].lstrip('/')

        if message.startswith('/'):
            return message.split(maxsplit=1)[0].lstrip('/')

        return None

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------

    async def send_notification(
        self,
        *,
        destination_id: str,
        text: str,
        thread_id: Optional[str] = None,
    ) -> Optional[str]:
        """Отправить сообщение в канал. destination_id — ID или имя канала."""
        try:
            resolved_id = await self.resolve_channel_id(destination_id)
            if not resolved_id:
                self.logger.error("Cannot resolve channel: %s", destination_id)
                return None

            post_data: Dict[str, Any] = {
                'channel_id': resolved_id,
                'message': text,
            }
            if thread_id:
                post_data['root_id'] = thread_id

            result = await asyncio.to_thread(self.driver.posts.create_post, post_data)
            msg_id = result.get('id')
            if msg_id:
                self.logger.debug("Message sent to %s: %s", resolved_id, msg_id)
            return msg_id

        except Exception as e:
            self.logger.error("Failed to send to %s: %s", destination_id, e)
            return None

    async def send_message(self, user: UserIdentity, text: str) -> Optional[str]:
        """Отправить прямое сообщение пользователю."""
        try:
            dm_channel = await asyncio.to_thread(
                self.driver.channels.create_direct_message_channel,
                [self._bot_user_id, user.user_id]
            )
            return await self.send_notification(destination_id=dm_channel['id'], text=text)
        except Exception as e:
            self.logger.error("Failed to send DM to %s: %s", user.user_id, e)
            return None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Получить информацию о пользователе Mattermost."""
        try:
            return await asyncio.to_thread(self.driver.users.get_user, user_id)
        except Exception as e:
            self.logger.error("Failed to get user %s: %s", user_id, e)
            return None

    async def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Получить информацию о канале."""
        try:
            return await asyncio.to_thread(self.driver.channels.get_channel, channel_id)
        except Exception as e:
            self.logger.error("Failed to get channel %s: %s", channel_id, e)
            return None

    async def resolve_channel_id(self, destination: str) -> Optional[str]:
        """
        Резолвить destination в channel_id.
        Если 26-символьный alphanumeric — вернуть как есть (уже ID).
        Иначе — найти канал по имени через API.
        """
        if len(destination) == 26 and destination.isalnum():
            return destination

        try:
            teams = await asyncio.to_thread(
                self.driver.teams.get_user_teams, self._bot_user_id,
            )
            for team in teams:
                try:
                    channel = await asyncio.to_thread(
                        self.driver.channels.get_channel_by_name,
                        team['id'], destination,
                    )
                    self.logger.debug(
                        "Resolved '%s' -> %s (team=%s)",
                        destination, channel['id'], team['display_name'],
                    )
                    return channel['id']
                except Exception:
                    continue

            self.logger.error("Channel '%s' not found in any team", destination)
            return None
        except Exception as e:
            self.logger.error("Failed to resolve channel '%s': %s", destination, e)
            return None


# Avoid import error for contextlib used in stop()
import contextlib  # noqa: E402
