# bot/utils/state_store.py
"""Хранилища состояния (state store).

Зачем этот модуль нужен
-----------------------
Боту нужно помнить часть состояния между рестартами (например, какой снэпшот очереди
уже отправляли в чат), чтобы не "спамить" одним и тем же сообщением при каждом запуске.

Изначально (шаг 23) состояние сохраняли в Redis. На шаге 24 добавляем важную вещь:
если Redis временно недоступен (сетевые проблемы, рестарт Redis-контейнера и т.п.),
бот НЕ должен падать. Вместо этого он должен продолжить работу, используя временное
in-memory хранилище.

Что лежит в store
-----------------
Store хранит небольшие JSON-объекты по ключу. Обычно это словари вида:
{
  "last_sent_snapshot": "...",
  "last_sent_ids": [1,2,3],
  ...
}

Какие переменные/поля важны
---------------------------
* active_backend / backend(): какой backend используется сейчас ("redis" или "memory")
* last_error: последняя ошибка работы с Redis (если была)
* last_ok_ts: когда Redis последний раз отвечал без ошибок (unix timestamp)

Важно: in-memory store НЕ переживает рестарт контейнера — он нужен только как
аварийный режим, чтобы бот оставался живым.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Optional, Protocol

import redis


class StateStore(Protocol):
    """Минимальный интерфейс, который нужен polling-логике."""

    def get_json(self, name: str) -> Optional[dict[str, Any]]:  # pragma: no cover (protocol)
        ...

    def set_json(self, name: str, value: dict[str, Any], ttl_s: Optional[int] = None) -> None:  # pragma: no cover
        ...

    def backend(self) -> str:  # pragma: no cover
        """"redis" или "memory" (или другое значение, если появятся новые backend'ы)."""

        ...


class RedisStateStore:
    """Хранилище состояния в Redis.

    Особенности:
    - хранит JSON строкой
    - ключи автоматически префиксуются (чтобы не конфликтовать с другими приложениями)
    - таймауты сокетов задаются явно, чтобы операции не "зависали" надолго
    """

    def __init__(
        self,
        redis_url: str,
        prefix: str = "testci",
        *,
        socket_timeout_s: float = 1.0,
        socket_connect_timeout_s: float = 1.0,
    ) -> None:
        self._r = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout_s,
            socket_connect_timeout=socket_connect_timeout_s,
        )
        self._prefix = prefix.rstrip(":")

    def _key(self, name: str) -> str:
        return f"{self._prefix}:{name}"

    def backend(self) -> str:
        return "redis"

    def ping(self) -> bool:
        """Быстрая проверка доступности Redis."""
        return bool(self._r.ping())

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


class MemoryStateStore:
    """Простейшее in-memory хранилище.

    Используется как fallback (аварийный режим), когда Redis недоступен.
    """

    def __init__(self, prefix: str = "testci") -> None:
        self._prefix = prefix.rstrip(":")
        self._data: dict[str, dict[str, Any]] = {}

    def _key(self, name: str) -> str:
        return f"{self._prefix}:{name}"

    def backend(self) -> str:
        return "memory"

    def get_json(self, name: str) -> Optional[dict[str, Any]]:
        # ВАЖНО: возвращаем копию, чтобы вызывающая сторона случайно не модифицировала
        # внутреннее состояние хранилища.
        v = self._data.get(self._key(name))
        return dict(v) if v is not None else None

    def set_json(self, name: str, value: dict[str, Any], ttl_s: Optional[int] = None) -> None:
        # TTL в памяти не реализуем — это аварийный режим.
        _ = ttl_s
        self._data[self._key(name)] = dict(value)


class ResilientStateStore:
    """Хранилище с автоматическим fallback.

    Идея:
    - Пытаемся читать/писать в Redis.
    - Если Redis упал/недоступен, не падаем сами, а используем MemoryStateStore.
    - При следующей успешной операции Redis считаем его восстановившимся.

    Диагностика:
    - last_error: текст последней ошибки Redis
    - last_ok_ts: когда Redis последний раз успешно отработал
    - active_backend: текущий активный backend ("redis" или "memory")
    """

    def __init__(self, primary: RedisStateStore, fallback: MemoryStateStore) -> None:
        self._primary = primary
        self._fallback = fallback

        self.active_backend: str = "redis"
        self.last_error: Optional[str] = None
        self.last_ok_ts: Optional[float] = None

    def backend(self) -> str:
        return self.active_backend

    def _mark_ok(self) -> None:
        self.active_backend = "redis"
        self.last_ok_ts = time.time()
        self.last_error = None

    def _mark_fail(self, e: Exception) -> None:
        self.active_backend = "memory"
        self.last_error = str(e)

    def ping(self) -> bool:
        """Пробуем ping'нуть Redis. Если не получилось — включаем memory режим."""
        try:
            ok = self._primary.ping()
            if ok:
                self._mark_ok()
            return ok
        except Exception as e:
            self._mark_fail(e)
            return False

    def get_json(self, name: str) -> Optional[dict[str, Any]]:
        try:
            v = self._primary.get_json(name)
            self._mark_ok()
            return v
        except Exception as e:
            self._mark_fail(e)
            return self._fallback.get_json(name)

    def set_json(self, name: str, value: dict[str, Any], ttl_s: Optional[int] = None) -> None:
        try:
            self._primary.set_json(name, value, ttl_s=ttl_s)
            self._mark_ok()
        except Exception as e:
            self._mark_fail(e)
            # В аварийном режиме всё равно сохраняем в память, чтобы поведение бота
            # было "ровным" внутри одного процесса.
            self._fallback.set_json(name, value, ttl_s=ttl_s)
