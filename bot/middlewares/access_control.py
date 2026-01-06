"""
Middleware контроля доступа для Telegram-бота.

Правила:
- админ имеет доступ ко всем командам;
- пользователь имеет доступ только к пользовательским командам;
- незарегистрированным показываем их id/аккаунт/ФИО/телефон.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message

from bot.services.user_store import TgProfile, UserStore


@dataclass(frozen=True)
class AccessPolicy:
    """
    Политика доступа для router/middleware.
    """
    required_role: str  # "admin" или "user"


class AccessControlMiddleware(BaseMiddleware):
    """
    Проверяет доступ по роли и при необходимости блокирует выполнение хендлера.
    """

    def __init__(self, *, policy: AccessPolicy) -> None:
        self._policy = policy

    async def __call__(self, handler, event: Message, data: dict[str, Any]):
        if not isinstance(event, Message):
            return await handler(event, data)

        user = event.from_user
        if user is None:
            return

        user_store: UserStore = data["user_store"]
        role = await user_store.get_role(user.id)

        if role is None:
            await _notify_unregistered(event)
            return

        # Обновляем профиль для зарегистрированных, чтобы данные были актуальны.
        profile = _profile_from_message(event)
        await user_store.update_profile(profile)

        if self._policy.required_role == "admin":
            if role != "admin":
                await event.answer("⛔ Доступ только для администраторов.")
                return
        else:
            # user router допускает и admin.
            if role not in ("user", "admin"):
                await event.answer("⛔ Доступ запрещён.")
                return

        return await handler(event, data)


async def _notify_unregistered(message: Message) -> None:
    """
    Сообщение незарегистрированному пользователю.
    """
    profile = _profile_from_message(message)
    lines = [
        "⛔ Вы не зарегистрированы в системе.",
        "",
        "Пожалуйста, перешлите это сообщение администратору для добавления:",
        f"- id: {profile.telegram_id}",
        f"- аккаунт: @{profile.username}" if profile.username else "- аккаунт: —",
        f"- ФИО: {profile.full_name}" if profile.full_name else "- ФИО: —",
        f"- телефон: {profile.phone}" if profile.phone else "- телефон: —",
    ]
    await message.answer("\n".join(lines))


def _profile_from_message(message: Message) -> TgProfile:
    """
    Извлекает профиль пользователя из сообщения.
    """
    user = message.from_user
    username = user.username or ""
    full_name = " ".join([x for x in [user.first_name, user.last_name] if x]).strip()
    phone = ""
    if message.contact and message.contact.user_id == user.id:
        phone = message.contact.phone_number or ""

    return TgProfile(
        telegram_id=user.id,
        username=username,
        full_name=full_name,
        phone=phone,
    )
