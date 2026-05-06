from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg


class PostgresStore:
    """PostgreSQL relational store backed by asyncpg."""

    def __init__(
        self,
        dsn: str = "",
        *,
        host: str = "",
        port: int = 5432,
        database: str = "",
        user: str = "",
        password: str = "",
        pool_min: int = 2,
        pool_max: int = 10,
    ) -> None:
        self._dsn = dsn
        self._host = host
        self._port = port
        self._database = database
        self._user = user
        self._password = password
        self._pool_min = pool_min
        self._pool_max = pool_max
        self._pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        if self._dsn:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=self._pool_min,
                max_size=self._pool_max,
                command_timeout=30,
                init=self._init_conn,
            )
        else:
            self._pool = await asyncpg.create_pool(
                host=self._host,
                port=self._port,
                database=self._database,
                user=self._user,
                password=self._password,
                min_size=self._pool_min,
                max_size=self._pool_max,
                command_timeout=30,
                init=self._init_conn,
            )

    @staticmethod
    async def _init_conn(conn: asyncpg.Connection) -> None:
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    async def execute(self, sql: str, *params: Any) -> int:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, *params)
            try:
                return int(result.split()[-1])
            except (ValueError, IndexError):
                return 0

    async def fetch_one(self, sql: str, *params: Any) -> dict | None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return dict(row) if row else None

    async def fetch_all(self, sql: str, *params: Any) -> list[dict]:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[PgTransaction]:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            tx = PgTransaction(conn)
            try:
                await tx.__aenter__()
                yield tx
                await tx.__aexit__(None, None, None)
            except Exception:
                await tx.__aexit__(*__import__("sys").exc_info())
                raise

    async def listen(self, channel: str) -> AsyncIterator[str]:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            queue: asyncio.Queue[str] = asyncio.Queue()
            await conn.add_listener(channel, lambda *args: queue.put_nowait(args[3]))
            try:
                while True:
                    yield await queue.get()
            finally:
                await conn.remove_listener(channel, lambda *args: queue.put_nowait(args[3]))

    async def notify(self, channel: str, payload: str = "") -> None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            safe_payload = payload.replace("'", "''")
            await conn.execute(f"NOTIFY {channel}, '{safe_payload}'")


class PgTransaction:
    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self._tx: Any = None

    async def __aenter__(self) -> PgTransaction:
        self._tx = self._conn.transaction()
        await self._tx.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._tx is None:
            return
        if args[0] is not None:
            await self._tx.rollback()
        else:
            await self._tx.commit()

    async def execute(self, sql: str, *params: Any) -> int:
        result = await self._conn.execute(sql, *params)
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def fetch_one(self, sql: str, *params: Any) -> dict | None:
        row = await self._conn.fetchrow(sql, *params)
        return dict(row) if row else None

    async def fetch_all(self, sql: str, *params: Any) -> list[dict]:
        rows = await self._conn.fetch(sql, *params)
        return [dict(r) for r in rows]
