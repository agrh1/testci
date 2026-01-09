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

    async def update_profile_if_exists(self, profile: TgProfile) -> None:
        """
        Обновляет профиль пользователя, если запись уже существует.
        """
        await asyncio.to_thread(self._update_profile_sync, profile)

    async def upsert_profile(self, profile: TgProfile, role: str) -> None:
        """
        Создаёт или обновляет профиль пользователя.
        """
        await asyncio.to_thread(self._upsert_profile_sync, profile, role)

    async def get_profile(self, telegram_id: int) -> Optional[TgProfile]:
        """
        Возвращает профиль пользователя или None.
        """
        return await asyncio.to_thread(self._get_profile_sync, telegram_id)

    async def delete_user(self, telegram_id: int) -> None:
        """
        Удаляет пользователя из таблицы.
        """
        await asyncio.to_thread(self._delete_user_sync, telegram_id)

    async def log_audit(self, *, telegram_id: int, action: str, actor_id: Optional[int]) -> None:
        """
        Логирует административные действия (U/D).
        """
        await asyncio.to_thread(self._log_audit_sync, telegram_id, action, actor_id)

    async def list_audit(self, telegram_id: int, limit: int = 20) -> list[dict[str, object]]:
        """
        Возвращает audit-историю пользователя.
        """
        return await asyncio.to_thread(self._list_audit_sync, telegram_id, limit)

    async def list_users(self, limit: int = 50) -> list[dict[str, object]]:
        """
        Возвращает список пользователей (ограниченно по количеству).
        """
        return await asyncio.to_thread(self._list_users_sync, limit)

    async def log_command(self, telegram_id: int, command: str) -> None:
        """
        Сохраняет команду в истории и обновляет last_command.
        """
        await asyncio.to_thread(self._log_command_sync, telegram_id, command)

    async def list_history(self, telegram_id: int, limit: int = 20) -> list[dict[str, object]]:
        """
        Возвращает историю команд пользователя (по убыванию времени).
        """
        return await asyncio.to_thread(self._list_history_sync, telegram_id, limit)

    async def top_by_last_activity(self, limit: int = 10) -> list[dict[str, object]]:
        """
        Топ по последнему обращению (last_command_at).
        """
        return await asyncio.to_thread(self._top_by_last_activity_sync, limit)

    async def top_by_frequency(self, limit: int = 10) -> list[dict[str, object]]:
        """
        Топ по частоте обращений (count в истории).
        """
        return await asyncio.to_thread(self._top_by_frequency_sync, limit)

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
                    last_command TEXT,
                    last_command_at TIMESTAMPTZ,
                    added_by BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            # Добавляем колонки, если таблица уже существовала.
            cur.execute("ALTER TABLE tg_users ADD COLUMN IF NOT EXISTS last_command TEXT")
            cur.execute("ALTER TABLE tg_users ADD COLUMN IF NOT EXISTS last_command_at TIMESTAMPTZ")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tg_command_history (
                    id BIGSERIAL PRIMARY KEY,
                    telegram_id BIGINT NOT NULL,
                    command TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tg_user_audit (
                    id BIGSERIAL PRIMARY KEY,
                    telegram_id BIGINT NOT NULL,
                    action TEXT NOT NULL,
                    actor_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
                SET username = COALESCE(NULLIF(%s, ''), username),
                    full_name = COALESCE(NULLIF(%s, ''), full_name),
                    phone = COALESCE(NULLIF(%s, ''), phone),
                    updated_at = now()
                WHERE telegram_id = %s
                """,
                (profile.username, profile.full_name, profile.phone, profile.telegram_id),
            )

    def _upsert_profile_sync(self, profile: TgProfile, role: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tg_users (telegram_id, role, username, full_name, phone)
                VALUES (%s, %s, NULLIF(%s, ''), NULLIF(%s, ''), NULLIF(%s, ''))
                ON CONFLICT (telegram_id)
                DO UPDATE SET
                    username = COALESCE(NULLIF(EXCLUDED.username, ''), tg_users.username),
                    full_name = COALESCE(NULLIF(EXCLUDED.full_name, ''), tg_users.full_name),
                    phone = COALESCE(NULLIF(EXCLUDED.phone, ''), tg_users.phone),
                    updated_at = now()
                """,
                (profile.telegram_id, role, profile.username, profile.full_name, profile.phone),
            )

    def _get_profile_sync(self, telegram_id: int) -> Optional[TgProfile]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT telegram_id, username, full_name, phone
                FROM tg_users
                WHERE telegram_id = %s
                """,
                (telegram_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return TgProfile(
                telegram_id=int(row["telegram_id"]),
                username=str(row["username"] or ""),
                full_name=str(row["full_name"] or ""),
                phone=str(row["phone"] or ""),
            )

    def _delete_user_sync(self, telegram_id: int) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM tg_users WHERE telegram_id = %s", (telegram_id,))

    def _list_users_sync(self, limit: int) -> list[dict[str, object]]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT telegram_id, role, username, full_name, phone, last_command, last_command_at
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
                    "last_command": str(r["last_command"] or ""),
                    "last_command_at": r["last_command_at"],
                }
                for r in rows
            ]

    def _log_command_sync(self, telegram_id: int, command: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tg_command_history (telegram_id, command)
                VALUES (%s, %s)
                """,
                (telegram_id, command),
            )
            cur.execute(
                """
                UPDATE tg_users
                SET last_command = %s, last_command_at = now(), updated_at = now()
                WHERE telegram_id = %s
                """,
                (command, telegram_id),
            )

    def _log_audit_sync(self, telegram_id: int, action: str, actor_id: Optional[int]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tg_user_audit (telegram_id, action, actor_id)
                VALUES (%s, %s, %s)
                """,
                (telegram_id, action, actor_id),
            )

    def _list_audit_sync(self, telegram_id: int, limit: int) -> list[dict[str, object]]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT action, actor_id, created_at
                FROM tg_user_audit
                WHERE telegram_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (telegram_id, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "action": str(r["action"]),
                    "actor_id": r["actor_id"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    def _list_history_sync(self, telegram_id: int, limit: int) -> list[dict[str, object]]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT command, created_at
                FROM tg_command_history
                WHERE telegram_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (telegram_id, limit),
            )
            rows = cur.fetchall()
            return [
                {
                    "command": str(r["command"]),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    def _top_by_last_activity_sync(self, limit: int) -> list[dict[str, object]]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT telegram_id, role, username, full_name, phone, last_command, last_command_at
                FROM tg_users
                WHERE last_command_at IS NOT NULL
                ORDER BY last_command_at DESC
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
                    "last_command": str(r["last_command"] or ""),
                    "last_command_at": r["last_command_at"],
                }
                for r in rows
            ]

    def _top_by_frequency_sync(self, limit: int) -> list[dict[str, object]]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT u.telegram_id, u.role, u.username, u.full_name, u.phone,
                       COUNT(h.id) AS cnt,
                       MAX(h.created_at) AS last_seen
                FROM tg_users u
                JOIN tg_command_history h ON h.telegram_id = u.telegram_id
                GROUP BY u.telegram_id, u.role, u.username, u.full_name, u.phone
                ORDER BY cnt DESC, last_seen DESC
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
                    "count": int(r["cnt"]),
                    "last_seen": r["last_seen"],
                }
                for r in rows
            ]
