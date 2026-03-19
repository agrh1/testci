"""
Хранилище пользователей бота в Postgres (platform_users).

Задачи:
- хранить mattermost_user_id и роль (admin/user);
- сохранять профиль (username, ФИО);
- логировать команды и аудит.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.extras


@dataclass(frozen=True)
class UserProfile:
    mattermost_user_id: str
    username: str
    full_name: str


class UserStore:
    """
    Хранилище пользователей на базе Postgres (таблица platform_users).
    """

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    async def get_role_by_mm_id(self, mattermost_user_id: str) -> Optional[str]:
        return await asyncio.to_thread(self._get_role_by_mm_id_sync, mattermost_user_id)

    async def upsert_user(
        self, *, mattermost_user_id: str, role: str, username: str = "", full_name: str = ""
    ) -> None:
        await asyncio.to_thread(self._upsert_user_sync, mattermost_user_id, role, username, full_name)

    async def update_profile(self, profile: UserProfile) -> None:
        await asyncio.to_thread(self._update_profile_sync, profile)

    async def get_profile(self, mattermost_user_id: str) -> Optional[UserProfile]:
        return await asyncio.to_thread(self._get_profile_sync, mattermost_user_id)

    async def delete_user(self, mattermost_user_id: str) -> None:
        await asyncio.to_thread(self._delete_user_sync, mattermost_user_id)

    async def list_users(self, limit: int = 50) -> list[dict[str, object]]:
        return await asyncio.to_thread(self._list_users_sync, limit)

    async def log_command(self, mattermost_user_id: str, command: str) -> None:
        await asyncio.to_thread(self._log_command_sync, mattermost_user_id, command)

    async def list_history(self, mattermost_user_id: str, limit: int = 20) -> list[dict[str, object]]:
        return await asyncio.to_thread(self._list_history_sync, mattermost_user_id, limit)

    async def log_audit(self, *, mattermost_user_id: str, action: str, actor_id: Optional[str]) -> None:
        await asyncio.to_thread(self._log_audit_sync, mattermost_user_id, action, actor_id)

    async def list_audit(self, mattermost_user_id: str, limit: int = 20) -> list[dict[str, object]]:
        return await asyncio.to_thread(self._list_audit_sync, mattermost_user_id, limit)

    async def top_by_last_activity(self, limit: int = 10) -> list[dict[str, object]]:
        return await asyncio.to_thread(self._top_by_last_activity_sync, limit)

    async def top_by_frequency(self, limit: int = 10) -> list[dict[str, object]]:
        return await asyncio.to_thread(self._top_by_frequency_sync, limit)

    def _connect(self):
        return psycopg2.connect(self._database_url)

    def _init_schema_sync(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_users (
                    id SERIAL PRIMARY KEY,
                    mattermost_user_id TEXT UNIQUE NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    username TEXT,
                    full_name TEXT,
                    last_command TEXT,
                    last_command_at TIMESTAMPTZ,
                    sync_status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute("ALTER TABLE platform_users ADD COLUMN IF NOT EXISTS last_command TEXT")
            cur.execute("ALTER TABLE platform_users ADD COLUMN IF NOT EXISTS last_command_at TIMESTAMPTZ")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_command_history (
                    id BIGSERIAL PRIMARY KEY,
                    mattermost_user_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_user_audit (
                    id BIGSERIAL PRIMARY KEY,
                    mattermost_user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def _get_role_by_mm_id_sync(self, mattermost_user_id: str) -> Optional[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT role FROM platform_users WHERE mattermost_user_id = %s",
                (mattermost_user_id,),
            )
            row = cur.fetchone()
            if row is not None:
                return str(row[0])
            return None

    def _upsert_user_sync(
        self, mattermost_user_id: str, role: str, username: str, full_name: str
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO platform_users (mattermost_user_id, role, username, full_name)
                VALUES (%s, %s, NULLIF(%s, ''), NULLIF(%s, ''))
                ON CONFLICT (mattermost_user_id)
                DO UPDATE SET
                    role = EXCLUDED.role,
                    username = COALESCE(NULLIF(EXCLUDED.username, ''), platform_users.username),
                    full_name = COALESCE(NULLIF(EXCLUDED.full_name, ''), platform_users.full_name),
                    updated_at = now()
                """,
                (mattermost_user_id, role, username, full_name),
            )

    def _update_profile_sync(self, profile: UserProfile) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE platform_users
                SET username = COALESCE(NULLIF(%s, ''), username),
                    full_name = COALESCE(NULLIF(%s, ''), full_name),
                    updated_at = now()
                WHERE mattermost_user_id = %s
                """,
                (profile.username, profile.full_name, profile.mattermost_user_id),
            )

    def _get_profile_sync(self, mattermost_user_id: str) -> Optional[UserProfile]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT mattermost_user_id, username, full_name
                FROM platform_users
                WHERE mattermost_user_id = %s
                """,
                (mattermost_user_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return UserProfile(
                mattermost_user_id=str(row["mattermost_user_id"]),
                username=str(row["username"] or ""),
                full_name=str(row["full_name"] or ""),
            )

    def _delete_user_sync(self, mattermost_user_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM platform_users WHERE mattermost_user_id = %s", (mattermost_user_id,))

    def _list_users_sync(self, limit: int) -> list[dict[str, object]]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT mattermost_user_id, role, username, full_name, last_command, last_command_at
                FROM platform_users
                ORDER BY role DESC, mattermost_user_id ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [
                {
                    "mattermost_user_id": str(r["mattermost_user_id"]),
                    "role": str(r["role"]),
                    "username": str(r["username"] or ""),
                    "full_name": str(r["full_name"] or ""),
                    "last_command": str(r["last_command"] or ""),
                    "last_command_at": r["last_command_at"],
                }
                for r in rows
            ]

    def _log_command_sync(self, mattermost_user_id: str, command: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO platform_command_history (mattermost_user_id, command)
                VALUES (%s, %s)
                """,
                (mattermost_user_id, command),
            )
            cur.execute(
                """
                UPDATE platform_users
                SET last_command = %s, last_command_at = now(), updated_at = now()
                WHERE mattermost_user_id = %s
                """,
                (command, mattermost_user_id),
            )

    def _log_audit_sync(self, mattermost_user_id: str, action: str, actor_id: Optional[str]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO platform_user_audit (mattermost_user_id, action, actor_id)
                VALUES (%s, %s, %s)
                """,
                (mattermost_user_id, action, actor_id),
            )

    def _list_audit_sync(self, mattermost_user_id: str, limit: int) -> list[dict[str, object]]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT action, actor_id, created_at
                FROM platform_user_audit
                WHERE mattermost_user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (mattermost_user_id, limit),
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

    def _list_history_sync(self, mattermost_user_id: str, limit: int) -> list[dict[str, object]]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT command, created_at
                FROM platform_command_history
                WHERE mattermost_user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (mattermost_user_id, limit),
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
                SELECT mattermost_user_id, role, username, full_name, last_command, last_command_at
                FROM platform_users
                WHERE last_command_at IS NOT NULL
                ORDER BY last_command_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [
                {
                    "mattermost_user_id": str(r["mattermost_user_id"]),
                    "role": str(r["role"]),
                    "username": str(r["username"] or ""),
                    "full_name": str(r["full_name"] or ""),
                    "last_command": str(r["last_command"] or ""),
                    "last_command_at": r["last_command_at"],
                }
                for r in rows
            ]

    def _top_by_frequency_sync(self, limit: int) -> list[dict[str, object]]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT u.mattermost_user_id, u.role, u.username, u.full_name,
                       COUNT(h.id) AS cnt,
                       MAX(h.created_at) AS last_seen
                FROM platform_users u
                JOIN platform_command_history h ON h.mattermost_user_id = u.mattermost_user_id
                GROUP BY u.mattermost_user_id, u.role, u.username, u.full_name
                ORDER BY cnt DESC, last_seen DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [
                {
                    "mattermost_user_id": str(r["mattermost_user_id"]),
                    "role": str(r["role"]),
                    "username": str(r["username"] or ""),
                    "full_name": str(r["full_name"] or ""),
                    "count": int(r["cnt"]),
                    "last_seen": r["last_seen"],
                }
                for r in rows
            ]
