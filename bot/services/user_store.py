"""
Хранилище пользователей/админов бота в Postgres.

Задачи:
- хранить telegram_id и роль (admin/user);
- сохранять профиль (username, ФИО, телефон);
- инициализировать роли из env при старте.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.extras


@dataclass(frozen=True)
class TgProfile:
    telegram_id: int
    username: str
    full_name: str
    phone: str


class UserStore:
    """
    Мини-хранилище пользователей на базе Postgres.
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def init_schema(self) -> None:
        """
        Создаёт таблицу, если её ещё нет.
        """
        await asyncio.to_thread(self._init_schema_sync)

    async def init_from_env(self, *, admins: tuple[int, ...], users: tuple[int, ...]) -> None:
        """
        Заполняет таблицу начальными данными из env.
        """
        await asyncio.to_thread(self._init_from_env_sync, admins, users)

    async def get_role(self, telegram_id: int) -> Optional[str]:
        """
        Возвращает роль пользователя, либо None.
        """
        return await asyncio.to_thread(self._get_role_sync, telegram_id)

    async def upsert_role(self, *, telegram_id: int, role: str, added_by: Optional[int]) -> None:
        """
        Создаёт или обновляет роль пользователя.
        """
        await asyncio.to_thread(self._upsert_role_sync, telegram_id, role, added_by)

    async def update_profile(self, profile: TgProfile) -> None:
        """
        Обновляет профиль пользователя, если запись уже существует.
        """
        await asyncio.to_thread(self._update_profile_sync, profile)

    async def delete_user(self, telegram_id: int) -> None:
        """
        Удаляет пользователя из таблицы.
        """
        await asyncio.to_thread(self._delete_user_sync, telegram_id)

    async def list_users(self, limit: int = 50) -> list[dict[str, object]]:
        """
        Возвращает список пользователей (ограниченно по количеству).
        """
        return await asyncio.to_thread(self._list_users_sync, limit)

    def _connect(self):
        return psycopg2.connect(self._database_url)

    def _init_schema_sync(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tg_users (
                    telegram_id BIGINT PRIMARY KEY,
                    role TEXT NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    phone TEXT,
                    added_by BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def _init_from_env_sync(self, admins: tuple[int, ...], users: tuple[int, ...]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            for tid in admins:
                cur.execute(
                    """
                    INSERT INTO tg_users (telegram_id, role, added_by)
                    VALUES (%s, 'admin', NULL)
                    ON CONFLICT (telegram_id)
                    DO UPDATE SET role = 'admin', updated_at = now()
                    """,
                    (tid,),
                )
            for tid in users:
                cur.execute(
                    """
                    INSERT INTO tg_users (telegram_id, role, added_by)
                    VALUES (%s, 'user', NULL)
                    ON CONFLICT (telegram_id)
                    DO UPDATE SET role = 'user', updated_at = now()
                    """,
                    (tid,),
                )

    def _get_role_sync(self, telegram_id: int) -> Optional[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT role FROM tg_users WHERE telegram_id = %s", (telegram_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return str(row[0])

    def _upsert_role_sync(self, telegram_id: int, role: str, added_by: Optional[int]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tg_users (telegram_id, role, added_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (telegram_id)
                DO UPDATE SET role = EXCLUDED.role, added_by = EXCLUDED.added_by, updated_at = now()
                """,
                (telegram_id, role, added_by),
            )

    def _update_profile_sync(self, profile: TgProfile) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tg_users
                SET username = NULLIF(%s, ''),
                    full_name = NULLIF(%s, ''),
                    phone = NULLIF(%s, ''),
                    updated_at = now()
                WHERE telegram_id = %s
                """,
                (profile.username, profile.full_name, profile.phone, profile.telegram_id),
            )

    def _delete_user_sync(self, telegram_id: int) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tg_users WHERE telegram_id = %s", (telegram_id,))

    def _list_users_sync(self, limit: int) -> list[dict[str, object]]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT telegram_id, role, username, full_name, phone
                FROM tg_users
                ORDER BY role DESC, telegram_id ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [
                {
                    "telegram_id": int(r["telegram_id"]),
                    "role": str(r["role"]),
                    "username": str(r["username"] or ""),
                    "full_name": str(r["full_name"] or ""),
                    "phone": str(r["phone"] or ""),
                }
                for r in rows
            ]
