from __future__ import annotations

import asyncio
import datetime
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg


def _pg_sql(sql: str) -> str:
    """Convert SQLite-style ? placeholders to PostgreSQL $1, $2, ..."""
    parts: list[str] = []
    counter = 0
    in_string = False
    for ch in sql:
        if ch == "'":
            in_string = not in_string
        if ch == "?" and not in_string:
            counter += 1
            parts.append(f"${counter}")
        else:
            parts.append(ch)
    return "".join(parts)


_TS_FLOOR = 1_000_000_000.0  # Unix timestamps are > 2001; scores/counts are not


def _pg_params(params: tuple[Any, ...]) -> tuple[Any, ...]:
    """Convert float Unix timestamps to datetime for PostgreSQL TIMESTAMPTZ columns."""
    return tuple(
        datetime.datetime.fromtimestamp(p, tz=datetime.timezone.utc)
        if isinstance(p, float) and p > _TS_FLOOR
        else p
        for p in params
    )


def _pg_row(row: dict | None) -> dict | None:
    """Convert datetime values back to float Unix timestamps for service compatibility."""
    if row is None:
        return None
    return {
        k: v.timestamp() if isinstance(v, datetime.datetime) else v
        for k, v in row.items()
    }


def _pg_rows(rows: list[dict]) -> list[dict]:
    return [_pg_row(r) for r in rows]  # type: ignore[arg-type]


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
            result = await conn.execute(_pg_sql(sql), *_pg_params(params))
            try:
                return int(result.split()[-1])
            except (ValueError, IndexError):
                return 0

    async def fetch_one(self, sql: str, *params: Any) -> dict | None:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_pg_sql(sql), *_pg_params(params))
            return _pg_row(dict(row)) if row else None

    async def fetch_all(self, sql: str, *params: Any) -> list[dict]:
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_pg_sql(sql), *_pg_params(params))
            return _pg_rows([dict(r) for r in rows])

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
        result = await self._conn.execute(_pg_sql(sql), *_pg_params(params))
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def fetch_one(self, sql: str, *params: Any) -> dict | None:
        row = await self._conn.fetchrow(_pg_sql(sql), *_pg_params(params))
        return _pg_row(dict(row)) if row else None

    async def fetch_all(self, sql: str, *params: Any) -> list[dict]:
        rows = await self._conn.fetch(_pg_sql(sql), *_pg_params(params))
        return _pg_rows([dict(r) for r in rows])
