"""
Работа с Postgres для хранения конфигурации бота + истории версий.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Optional, Tuple

from sqlalchemy import Column, DateTime, Integer, Text, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class BotConfig(Base):
    __tablename__ = "bot_config"

    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False)
    config_json = Column(Text, nullable=False)


class BotConfigHistory(Base):
    __tablename__ = "bot_config_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(Integer, nullable=False)
    config_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    comment = Column(Text, nullable=True)


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def db_enabled() -> bool:
    return bool(_database_url())


def create_db_engine() -> Engine:
    return create_engine(_database_url(), pool_pre_ping=True, future=True)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)

    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        row = s.get(BotConfig, 1)
        if row is None:
            s.add(
                    BotConfig(
                        id=1,
                        version=1,
                        config_json=json.dumps(
                            {
                                "routing": {"rules": [], "default_dest": {}},
                                "eventlog": {"rules": [], "default_dest": {}},
                                "escalation": {"enabled": False},
                            },
                            ensure_ascii=False,
                        ),
                    )
            )
            s.commit()


def read_config(engine: Engine) -> Tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        Session = sessionmaker(bind=engine, future=True)
        with Session() as s:
            row = s.get(BotConfig, 1)
            if not row:
                return None, "config not found"

            data = json.loads(row.config_json)
            data["version"] = row.version
            return data, None
    except Exception as e:
        return None, str(e)


def write_config(engine: Engine, cfg: dict[str, Any], comment: str | None = None) -> int:
    """
    Сохраняет новый конфиг:
    - старую версию кладёт в history
    - увеличивает version
    """
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        current = s.get(BotConfig, 1)
        if not current:
            raise RuntimeError("config row missing")

        # сохранить текущую версию в history
        s.add(
            BotConfigHistory(
                version=current.version,
                config_json=current.config_json,
                comment=comment,
            )
        )

        # обновить текущую
        current.version += 1
        current.config_json = json.dumps(cfg, ensure_ascii=False)

        s.commit()
        return current.version


def list_history(engine: Engine, limit: int = 20) -> list[dict[str, Any]]:
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        rows = (
            s.query(BotConfigHistory)
            .order_by(BotConfigHistory.version.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "version": r.version,
                "created_at": r.created_at.isoformat(),
                "comment": r.comment,
            }
            for r in rows
        ]


def get_config_by_version(engine: Engine, version: int) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    Возвращает конфиг по версии.

    Источник:
    - текущий config (bot_config) если версия совпадает;
    - история (bot_config_history) для старых версий.
    """
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        current = s.get(BotConfig, 1)
        if current and current.version == version:
            data = json.loads(current.config_json)
            data["version"] = current.version
            return data, None

        hist = (
            s.query(BotConfigHistory)
            .filter(BotConfigHistory.version == version)
            .one_or_none()
        )
        if not hist:
            return None, f"version {version} not found"

        data = json.loads(hist.config_json)
        data["version"] = hist.version
        return data, None


def count_rollbacks_since(engine: Engine, since_dt: datetime) -> tuple[int, Optional[datetime]]:
    """
    Возвращает количество rollback за период и время последнего rollback.
    """
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        rows = (
            s.query(BotConfigHistory)
            .filter(BotConfigHistory.created_at >= since_dt)
            .filter(BotConfigHistory.comment.like("rollback%"))
            .order_by(BotConfigHistory.created_at.desc())
            .all()
        )
        if not rows:
            return 0, None
        return len(rows), rows[0].created_at


def rollback_to_version(engine: Engine, version: int) -> int:
    """
    Делает rollback:
    - берёт config_json из history
    - записывает его как новую текущую версию
    """
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        hist = (
            s.query(BotConfigHistory)
            .filter(BotConfigHistory.version == version)
            .one_or_none()
        )
        if not hist:
            raise RuntimeError(f"history version {version} not found")

        current = s.get(BotConfig, 1)
        if not current:
            raise RuntimeError("config row missing")

        # сохранить текущую в history
        s.add(
            BotConfigHistory(
                version=current.version,
                config_json=current.config_json,
                comment=f"rollback from v{current.version} to v{version}",
            )
        )

        current.version += 1
        current.config_json = hist.config_json

        s.commit()
        return current.version
