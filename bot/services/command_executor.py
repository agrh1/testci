"""
Command executor service - унифицированный исполнитель команд для всех платформ.

Этот модуль предоставляет:
- CommandRequest: унифицированный запрос команды (от TG или MM)
- CommandResponse: унифицированный ответ на команду
- CommandExecutor: главный исполнитель команд (переходный адаптер между платформами)
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from bot.adapters.base import MessageAdapter, StateManager, UserIdentity


@dataclass
class CommandRequest:
    """
    Унифицированный запрос команды (не привязан к платформе).

    Используется как промежуточное представление для
    платформо-независимого обработчика команды.
    """

    # Информация о пользователе
    user: UserIdentity

    # Информация о команде
    command: str  # имя команды без слэша, например "start", "status"
    args: Dict[str, Any] = field(default_factory=dict)  # аргументы команды

    # Контекст (зависит от платформы)
    # Для Mattermost: содержит информацию о команде из WebSocket
    context: Dict[str, Any] = field(default_factory=dict)

    # Метаинформация
    raw_text: str = ""  # исходный текст команды (для логирования)
    is_admin: bool = False  # определено ли что пользователь админ


@dataclass
class CommandResponse:
    """
    Унифицированный ответ на команду.

    Может быть обработан любым адаптером для отправки пользователю.
    """

    text: str  # основное сообщение ответа

    ok: bool = True  # удалась ли команда

    # Опциональные поля
    reply_to: Optional[str] = None  # ID сообщения к которому отвечаем
    attachments: Optional[list] = None  # файлы, картинки, ссылки
    keyboard: Optional[Any] = None  # кнопки/клавиатура (зависит от платформы)
    thread_id: Optional[str] = None  # для threading (Telegram: message_thread_id, MM: root_id)

    # Дополнительные опции
    edit_existing: Optional[str] = None  # ID сообщения для редактирования (если поддерживается)
    delete_existing: Optional[str] = None  # ID сообщения для удаления

    @classmethod
    def error(cls, text: str) -> CommandResponse:
        """Быстрое создание ответа об ошибке."""
        return cls(text=text, ok=False)

    @classmethod
    def success(cls, text: str) -> CommandResponse:
        """Быстрое создание успешного ответа."""
        return cls(text=text, ok=True)


class CommandExecutor:
    """
    Главный исполнитель команд - координирует обработку команд всеми платформами.

    Задачи:
    1. Маршрутизация команды к правильному обработчику
    2. Проверка прав доступа (admin vs user)
    3. Управление состоянием команды (multi-step commands)
    4. Обработка ошибок и fallback логика
    5. Интеграция с адаптерами для отправки ответов
    """

    def __init__(
        self,
        adapters: Dict[str, MessageAdapter],
        state_managers: Dict[str, StateManager],
        db: Any = None,  # database connection
        logger: Any = None,
    ):
        """
        Инициализация executor'а.

        Args:
            adapters: dict с адаптерами для каждой платформы
                     {"mattermost": MattermostMessageAdapter}
            state_managers: dict с менеджерами состояния для каждой платформы
            db: подключение к БД (для проверки ролей, истории команд и т.д.)
            logger: логгер для отладки
        """
        self._adapters = adapters
        self._state_managers = state_managers
        self._db = db
        self._logger = logger

        # Реестр обработчиков команд (заполняется при регистрации)
        self._handlers: Dict[str, Callable] = {}

        # Реестр зависимостей для инъекции в команды
        self._dependencies: Dict[str, Any] = {}

        # Команды требующие админ доступа
        self._admin_commands = {
            "status",
            "user_add",
            "user_remove",
            "admin_add",
            "admin_remove",
            "config",
            "config_diff",
            "routes_test",
            "routes_debug",
            "routes_send_test",
            "escalation_send_test",
            "eventlog_poll",
            "eventlog_filters",
            "service_icons",
            "service_icon_add",
        }

    def register_handler(self, command: str, handler: Callable) -> None:
        """
        Зарегистрировать обработчик команды.

        Args:
            command: имя команды (например "start", "help")
            handler: async callable(request: CommandRequest, **kwargs) -> CommandResponse
        """
        self._handlers[command.lower()] = handler
        if self._logger:
            self._logger.debug(f"Registered handler for command: {command}")

    def set_dependency(self, name: str, value: Any) -> None:
        """
        Зарегистрировать зависимость для инъекции в команды.

        Args:
            name: имя зависимости (например "config_sync", "runtime_config")
            value: значение/объект зависимости
        """
        self._dependencies[name] = value
        if self._logger:
            self._logger.debug(f"Registered dependency: {name}")

    def set_dependencies(self, deps: Dict[str, Any]) -> None:
        """Зарегистрировать несколько зависимостей одновременно."""
        self._dependencies.update(deps)
        if self._logger:
            self._logger.debug(f"Registered {len(deps)} dependencies")

    async def execute(self, request: CommandRequest) -> CommandResponse:
        """
        Выполнить команду.

        Процесс:
        1. Проверить доступ (админ команды)
        2. Получить обработчик команды
        3. Управление состоянием (если многошаговая команда)
        4. Выполнить обработчик (с инъекцией зависимостей)
        5. Отправить ответ через адаптер

        Args:
            request: унифицированный запрос команды

        Returns:
            CommandResponse с результатом выполнения
        """
        command = request.command.lower()

        # Логировать команду
        if self._logger:
            self._logger.info(f"Executing command '{command}' for user {request.user.user_id}")

        # 1. Проверить права доступа
        if command in self._admin_commands:
            if not request.is_admin:
                return CommandResponse.error("❌ You don't have permission to execute this command")

        # 2. Получить обработчик
        handler = self._handlers.get(command)
        if handler is None:
            return CommandResponse.error(f"❌ Unknown command: /{command}")

        try:
            # 3. Выполнить обработчик с инъекцией зависимостей
            # Передаём только те зависимости, которые handler принимает
            sig = inspect.signature(handler)
            params = sig.parameters
            if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
                # Handler принимает **kwargs — передаём всё
                kwargs = self._dependencies
            else:
                # Фильтруем по именам параметров
                kwargs = {
                    k: v for k, v in self._dependencies.items()
                    if k in params
                }
            response = await handler(request, **kwargs)
            if response is None:
                response = CommandResponse(text="Command executed successfully")

            # NOTE: Ответ НЕ отправляем здесь — это делает вызывающая сторона (bot_app.py).
            # Отправка здесь приводила бы к двойной отправке (DM + ответ в канал).

            return response

        except Exception as e:
            if self._logger:
                self._logger.error(
                    f"Error executing command '{command}': {type(e).__name__}: {e}",
                    exc_info=True,
                )
            return CommandResponse.error(f"❌ Error ({type(e).__name__}): {str(e)}")

    async def get_user_state(self, user: UserIdentity) -> Optional[Dict[str, Any]]:
        """Получить состояние пользователя (для многошаговых команд)."""
        state_manager = self._state_managers.get(user.platform)
        if state_manager:
            return await state_manager.get_state(user)
        return None

    async def set_user_state(self, user: UserIdentity, state: Dict[str, Any]) -> None:
        """Сохранить состояние пользователя."""
        state_manager = self._state_managers.get(user.platform)
        if state_manager:
            await state_manager.set_state(user, state)

    async def clear_user_state(self, user: UserIdentity) -> None:
        """Очистить состояние пользователя."""
        state_manager = self._state_managers.get(user.platform)
        if state_manager:
            await state_manager.clear_state(user)

    def is_admin(self, user: UserIdentity) -> bool:
        """
        Проверить является ли пользователь админом.

        TODO: Реализовать проверку в БД (web.db.PlatformUser)
        """
        # На данный момент - просто трубка для интеграции с БД
        # TODO: SELECT role FROM platform_users WHERE mattermost_user_id
        return False
