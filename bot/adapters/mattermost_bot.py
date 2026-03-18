"""
Mattermost Bot Adapter - интеграция через Bot Account + WebSocket.

Этот адаптер подключается к Mattermost как бот (через WebSocket) и слушает сообщения.
Вместо Slash Commands используем команды в обычных сообщениях: /command arg1 arg2

Возможности:
- Слушать сообщения с командами (начинающиеся с /)
- Отправлять сообщения в каналы и в ответ на конкретные посты
- Поддержка threading (root_id для ответов в ветках)
- Автоматическое подключение при запуске приложения
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse

try:
    from mattermostdriver import Driver
except ImportError as e:
    Driver = None  # type: ignore
    _import_error = e

from bot.adapters.base import UserIdentity


class MattermostBotAdapter:
    """
    Мattermost Bot интеграция через Bot Account + WebSocket.

    Подключается к Mattermost как бот и слушает все сообщения в каналах,
    автоматически парсит команды и отправляет их в CommandExecutor.
    """

    def __init__(
        self,
        *,
        server_url: str,
        bot_token: str,
        logger: logging.Logger,
        on_command: Optional[Callable] = None,
    ):
        """
        Инициализация Mattermost Bot адаптера.

        Args:
            server_url: URL Mattermost сервера (например https://mattermost.example.com)
            bot_token: Bot Account token из System Console
            logger: логгер для отладки
            on_command: callback функция для обработки команд
                       signature: async on_command(command: str, text: str, user_id: str, channel_id: str, post_id: str)
        """
        if Driver is None:
            raise ImportError("Install mattermostdriver: pip install mattermostdriver")

        self.server_url = server_url
        self.bot_token = bot_token
        self.logger = logger
        self.on_command = on_command

        # Инициализируем Mattermost клиент
        # Parse the server URL to extract host, port, and scheme
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

        # WebSocket listener будет запущен в отдельной корутине
        self._ws_task: Optional[asyncio.Task] = None
        self._is_running = False
        self._bot_user_id: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def start(self) -> None:
        """Запустить бота (подключиться к WebSocket и слушать)."""
        if self._is_running:
            self.logger.warning("MattermostBotAdapter is already running")
            return

        try:
            # Захватить event loop для thread-safe вызовов из WebSocket callback
            self._loop = asyncio.get_running_loop()

            # Авторизоваться (устанавливает token на HTTP-клиент)
            await asyncio.to_thread(self.driver.login)

            # Получить информацию о самом боте
            me = await asyncio.to_thread(self.driver.users.get_user, 'me')
            self._bot_user_id = me['id']
            self.logger.info(f"✓ Mattermost bot connected: {me['username']} (id={self._bot_user_id})")

            self._is_running = True

            # Запустить WebSocket listener в отдельной задаче
            self._ws_task = asyncio.create_task(self._listen_websocket())
            self.logger.info("✓ WebSocket listener started")

        except Exception as e:
            self.logger.error(f"❌ Failed to start MattermostBotAdapter: {e}", exc_info=True)
            raise

    async def stop(self) -> None:
        """Остановить бота и закрыть WebSocket."""
        self._is_running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        self.logger.info("✓ MattermostBotAdapter stopped")

    async def _listen_websocket(self) -> None:
        """
        Слушать WebSocket события от Mattermost.

        Фильтруем события:
        - posted: новое сообщение
        - Игнорируем собственные сообщения (от самого бота)
        - Ищем команды (сообщения начинающиеся с /)
        """
        try:
            # Подключиться к WebSocket (синхронный вызов, поэтому в отдельном потоке)
            # mattermostdriver внутри вызывает asyncio.get_event_loop(),
            # поэтому нужно создать event loop в рабочем потоке
            def _run_ws():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    self.driver.init_websocket(self._handle_websocket_message)
                finally:
                    loop.close()

            await asyncio.to_thread(_run_ws)
        except Exception as e:
            self.logger.error(f"❌ WebSocket error: {e}", exc_info=True)
            if self._is_running:
                # Автоматический переподключение через 5 секунд
                self.logger.info("Attempting to reconnect in 5 seconds...")
                await asyncio.sleep(5)
                if self._is_running:
                    await self._listen_websocket()

    def _handle_websocket_message(self, msg: Dict[str, Any]) -> None:
        """
        Обработать сообщение от Mattermost WebSocket.

        Вызывается синхронно из mattermostdriver, поэтому мы должны запланировать
        async задачу в event loop.
        """
        try:
            event = msg.get('event', '')

            # Нас интересуют только события posted (новые сообщения)
            if event != 'posted':
                return

            # Распаковать данные поста
            data = msg.get('data', {})
            post_str = data.get('post', '')
            if not post_str:
                return

            post = json.loads(post_str)
            user_id = post.get('user_id', '')
            channel_id = post.get('channel_id', '')
            message = post.get('message', '').strip()
            post_id = post.get('id', '')

            # Игнорировать собственные сообщения
            if user_id == self._bot_user_id:
                return

            # Проверить, это команда?
            if not message.startswith('/'):
                return

            # Парсим команду
            parts = message.split(maxsplit=1)
            command = parts[0].lstrip('/')
            raw_text = message

            self.logger.debug(
                f"Received command from {user_id}: /{command} in {channel_id}"
            )

            # Запланировать обработку команды в event loop (thread-safe)
            if self.on_command and self._loop:
                asyncio.run_coroutine_threadsafe(
                    self.on_command(
                        command=command,
                        text=raw_text,
                        user_id=user_id,
                        channel_id=channel_id,
                        post_id=post_id,
                    ),
                    self._loop,
                )

        except Exception as e:
            self.logger.error(
                f"Error handling WebSocket message: {e}",
                exc_info=True
            )

    async def send_notification(
        self,
        *,
        destination_id: str,
        text: str,
        thread_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        Отправить сообщение в канал (реализует MessageAdapter протокол).

        Args:
            destination_id: Mattermost channel ID
            text: текст сообщения
            thread_id: post ID для threading (root_id в Mattermost)

        Returns:
            post ID если успешно, иначе None
        """
        try:
            post_data = {
                'channel_id': destination_id,
                'message': text,
            }
            if thread_id:
                post_data['root_id'] = thread_id

            result = await asyncio.to_thread(
                self.driver.posts.create_post,
                post_data
            )
            msg_id = result.get('id')
            if msg_id:
                self.logger.debug(
                    f"Message sent to {destination_id}: {msg_id}"
                )
            return msg_id

        except Exception as e:
            self.logger.error(
                f"Failed to send message to {destination_id}: {e}",
                exc_info=True
            )
            return None

    async def send_message(self, user: UserIdentity, text: str) -> Optional[str]:
        """
        Отправить прямое сообщение пользователю (реализует MessageAdapter протокол).

        Args:
            user: UserIdentity объект (содержит user_id)
            text: текст сообщения

        Returns:
            channel ID если успешно, иначе None
        """
        try:
            # Создать DM канал с пользователем
            dm_channel = await asyncio.to_thread(
                self.driver.channels.create_direct_message_channel,
                [user.user_id]
            )
            channel_id = dm_channel['id']

            # Отправить сообщение в DM
            return await self.send_notification(
                destination_id=channel_id,
                text=text,
            )

        except Exception as e:
            self.logger.error(
                f"Failed to send DM to {user.user_id}: {e}",
                exc_info=True
            )
            return None

    async def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Получить информацию о пользователе Mattermost.

        Returns:
            dict с полями: id, username, first_name, last_name, etc.
        """
        try:
            user = await asyncio.to_thread(
                self.driver.users.get_user,
                user_id
            )
            return user
        except Exception as e:
            self.logger.error(f"Failed to get user info {user_id}: {e}")
            return None

    async def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Получить информацию о канале."""
        try:
            channel = await asyncio.to_thread(
                self.driver.channels.get_channel,
                channel_id
            )
            return channel
        except Exception as e:
            self.logger.error(f"Failed to get channel info {channel_id}: {e}")
            return None
