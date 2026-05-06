from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis


class RedisCache:
    """Redis-backed cache with JSON serialization."""

    def __init__(
        self,
        url: str = "",
        *,
        host: str = "",
        port: int = 6379,
        password: str = "",
        db: int = 0,
    ) -> None:
        self._url = url
        self._host = host
        self._port = port
        self._password = password
        self._db = db
        self._client: redis.Redis | None = None

    async def init(self) -> None:
        if self._url:
            self._client = redis.from_url(self._url, decode_responses=False)
        else:
            self._client = redis.Redis(
                host=self._host,
                port=self._port,
                password=self._password or None,
                db=self._db,
                decode_responses=False,
            )
        await self._client.ping()  # type: ignore[misc]

    async def get(self, key: str) -> Any | None:
        assert self._client is not None
        raw = await self._client.get(key)
        return json.loads(raw) if raw else None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        assert self._client is not None
        data = json.dumps(value)
        if ttl:
            await self._client.setex(key, ttl, data)
        else:
            await self._client.set(key, data)

    async def delete(self, key: str) -> None:
        assert self._client is not None
        await self._client.delete(key)

    async def incr(self, key: str, amount: int = 1, ttl: int | None = None) -> int:
        assert self._client is not None
        async with self._client.pipeline() as pipe:
            pipe.incrby(key, amount)
            if ttl:
                pipe.expire(key, ttl)
            results = await pipe.execute()
        return results[0]
