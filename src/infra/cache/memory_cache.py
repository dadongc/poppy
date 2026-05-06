from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any


class MemoryCache:
    """In-memory LRU cache for local development."""

    def __init__(self, max_size: int = 1000) -> None:
        self._max_size = max_size
        self._store: OrderedDict[str, tuple[Any, float | None]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        pass

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            if key not in self._store:
                return None
            value, expire_at = self._store[key]
            if expire_at is not None and time.monotonic() > expire_at:
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        async with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            else:
                while len(self._store) >= self._max_size:
                    self._store.popitem(last=False)

            expire_at = time.monotonic() + ttl if ttl is not None else None
            self._store[key] = (value, expire_at)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def incr(self, key: str, amount: int = 1, ttl: int | None = None) -> int:
        async with self._lock:
            current = 0
            expire_at = None
            if key in self._store:
                val, expire_at = self._store[key]
                current = int(val) if isinstance(val, (int, float)) else 0
            current += amount
            if ttl is not None:
                expire_at = time.monotonic() + ttl
            self._store[key] = (current, expire_at)
            self._store.move_to_end(key)
            return current
