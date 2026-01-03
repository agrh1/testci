# bot/utils/state_store.py
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any, Optional

import redis


class RedisStateStore:
    """
    Простейшее хранилище состояния в Redis.

    Хранит JSON строкой по ключу.
    Подходит для небольших объектов (наше состояние polling).
    """

    def __init__(self, redis_url: str, prefix: str = "testci") -> None:
        self._r = redis.Redis.from_url(redis_url, decode_responses=True)
        self._prefix = prefix.rstrip(":")

    def _key(self, name: str) -> str:
        return f"{self._prefix}:{name}"

    def get_json(self, name: str) -> Optional[dict[str, Any]]:
        raw = self._r.get(self._key(name))
        if not raw:
            return None
        return json.loads(raw)

    def set_json(self, name: str, value: dict[str, Any], ttl_s: Optional[int] = None) -> None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        key = self._key(name)
        if ttl_s is None:
            self._r.set(key, raw)
        else:
            self._r.setex(key, ttl_s, raw)

    @staticmethod
    def dataclass_to_dict(obj: Any) -> dict[str, Any]:
        if is_dataclass(obj):
            return asdict(obj)
        raise TypeError("Expected dataclass instance")
