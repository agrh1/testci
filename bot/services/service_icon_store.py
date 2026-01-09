"""
Хранилище значков сервисов для отображения в сообщениях.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import psycopg2
import psycopg2.extras


@dataclass(frozen=True)
class ServiceIcon:
    service_id: int
    service_code: str
    service_name: str
    icon: str
    enabled: bool


class ServiceIconStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self):
        return psycopg2.connect(self._database_url)

    async def init_schema(self) -> None:
        await asyncio.to_thread(self._init_schema_sync)

    async def list_enabled(self) -> list[ServiceIcon]:
        return await asyncio.to_thread(self._list_enabled_sync)

    async def list_all(self, *, limit: int = 100) -> list[ServiceIcon]:
        return await asyncio.to_thread(self._list_all_sync, limit)

    async def upsert_icon(
        self,
        *,
        service_id: int,
        service_code: str,
        icon: str,
        service_name: str = "",
        enabled: bool = True,
    ) -> None:
        await asyncio.to_thread(
            self._upsert_icon_sync,
            service_id,
            service_code,
            icon,
            service_name,
            enabled,
        )

    def _init_schema_sync(self) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS service_icons (
                    id SERIAL PRIMARY KEY,
                    service_id INTEGER UNIQUE NOT NULL,
                    service_code TEXT NOT NULL,
                    service_name TEXT,
                    icon TEXT NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def _row_to_icon(self, row) -> ServiceIcon:
        return ServiceIcon(
            service_id=int(row["service_id"]),
            service_code=str(row["service_code"] or ""),
            service_name=str(row["service_name"] or ""),
            icon=str(row["icon"] or ""),
            enabled=bool(row["enabled"]),
        )

    def _list_enabled_sync(self) -> list[ServiceIcon]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT service_id, service_code, service_name, icon, enabled
                FROM service_icons
                WHERE enabled = TRUE
                ORDER BY service_id ASC
                """
            )
            rows = cur.fetchall()
            return [self._row_to_icon(r) for r in rows]

    def _list_all_sync(self, limit: int) -> list[ServiceIcon]:
        with self._connect() as conn, conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(
                """
                SELECT service_id, service_code, service_name, icon, enabled
                FROM service_icons
                ORDER BY service_id ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [self._row_to_icon(r) for r in rows]

    def _upsert_icon_sync(
        self,
        service_id: int,
        service_code: str,
        icon: str,
        service_name: str,
        enabled: bool,
    ) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO service_icons (service_id, service_code, service_name, icon, enabled)
                VALUES (%s, %s, NULLIF(%s, ''), %s, %s)
                ON CONFLICT (service_id)
                DO UPDATE SET
                    service_code = EXCLUDED.service_code,
                    service_name = COALESCE(NULLIF(EXCLUDED.service_name, ''), service_icons.service_name),
                    icon = EXCLUDED.icon,
                    enabled = EXCLUDED.enabled,
                    updated_at = now()
                """,
                (service_id, service_code, service_name, icon, enabled),
            )
