"""
Хранилище настроек Seafile сервисов в Postgres.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.extras


@dataclass(frozen=True)
class SeafileService:
    service_id: int
    name: str
    base_url: str
    repo_id: str
    auth_token: str
    username: str
    password: str
    enabled: bool


class SeafileServiceStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self):
        return psycopg2.connect(self._database_url)

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    async def list_services(self, *, enabled_only: bool = True) -> list[SeafileService]:
        return await asyncio.to_thread(self._list_services_sync, enabled_only)

    async def get_service(self, service_id: int) -> Optional[SeafileService]:
        return await asyncio.to_thread(self._get_service_sync, service_id)

    def _init_schema_sync(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS seafile_services (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    auth_token TEXT,
                    username TEXT,
                    password TEXT,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def _row_to_service(self, row) -> SeafileService:
        return SeafileService(
            service_id=int(row["id"]),
            name=str(row["name"] or ""),
            base_url=str(row["base_url"] or ""),
            repo_id=str(row["repo_id"] or ""),
            auth_token=str(row["auth_token"] or ""),
            username=str(row["username"] or ""),
            password=str(row["password"] or ""),
            enabled=bool(row["enabled"]),
        )

    def _list_services_sync(self, enabled_only: bool) -> list[SeafileService]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            if enabled_only:
                cur.execute(
                    """
                    SELECT id, name, base_url, repo_id, auth_token, username, password, enabled
                    FROM seafile_services
                    WHERE enabled = TRUE
                    ORDER BY id ASC
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT id, name, base_url, repo_id, auth_token, username, password, enabled
                    FROM seafile_services
                    ORDER BY id ASC
                    """
                )
            rows = cur.fetchall()
            return [self._row_to_service(r) for r in rows]

    def _get_service_sync(self, service_id: int) -> Optional[SeafileService]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT id, name, base_url, repo_id, auth_token, username, password, enabled
                FROM seafile_services
                WHERE id = %s
                """,
                (service_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return self._row_to_service(row)

