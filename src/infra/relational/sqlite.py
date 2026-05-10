from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class SqliteStore:
    """SQLite relational store for local development and testing.

    All operations are serialized through an asyncio.Lock and run on the
    calling thread (no thread pool) to avoid Python 3.13+ free-threading
    issues with sqlite3.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        import os

        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    async def close(self) -> None:
        async with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    @staticmethod
    def _adapt(sql: str, params: tuple) -> tuple[str, tuple]:
        import re
        refs = re.findall(r'\$(\d+)', sql)
        if not refs:
            return sql, params
        indices = [int(r) - 1 for r in refs]
        return re.sub(r'\$(\d+)', '?', sql), tuple(params[i] for i in indices)

    async def execute(self, sql: str, *params: Any) -> int:
        async with self._lock:
            assert self._conn is not None
            cur = self._conn.cursor()
            adapted_sql, adapted_params = self._adapt(sql, params)
            cur.execute(adapted_sql, adapted_params)
            self._conn.commit()
            return cur.rowcount

    async def fetch_one(self, sql: str, *params: Any) -> dict | None:
        async with self._lock:
            assert self._conn is not None
            cur = self._conn.cursor()
            adapted_sql, adapted_params = self._adapt(sql, params)
            cur.execute(adapted_sql, adapted_params)
            row = cur.fetchone()
            return dict(row) if row else None

    async def fetch_all(self, sql: str, *params: Any) -> list[dict]:
        async with self._lock:
            assert self._conn is not None
            cur = self._conn.cursor()
            adapted_sql, adapted_params = self._adapt(sql, params)
            cur.execute(adapted_sql, adapted_params)
            return [dict(row) for row in cur.fetchall()]

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SqliteTransaction]:
        async with self._lock:
            assert self._conn is not None
            self._conn.execute("BEGIN")
            tx = SqliteTransaction(self._conn)
            try:
                yield tx
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    async def listen(self, channel: str) -> AsyncIterator[str]:
        if False:
            yield ""

    async def notify(self, channel: str, payload: str = "") -> None:
        pass


class SqliteTransaction:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _adapt(sql: str, params: tuple) -> tuple[str, tuple]:
        import re
        refs = re.findall(r'\$(\d+)', sql)
        if not refs:
            return sql, params
        indices = [int(r) - 1 for r in refs]
        return re.sub(r'\$(\d+)', '?', sql), tuple(params[i] for i in indices)

    async def execute(self, sql: str, *params: Any) -> int:
        cur = self._conn.cursor()
        adapted_sql, adapted_params = self._adapt(sql, params)
        cur.execute(adapted_sql, adapted_params)
        return cur.rowcount

    async def fetch_one(self, sql: str, *params: Any) -> dict | None:
        cur = self._conn.cursor()
        adapted_sql, adapted_params = self._adapt(sql, params)
        cur.execute(adapted_sql, adapted_params)
        row = cur.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql: str, *params: Any) -> list[dict]:
        cur = self._conn.cursor()
        adapted_sql, adapted_params = self._adapt(sql, params)
        cur.execute(adapted_sql, adapted_params)
        return [dict(row) for row in cur.fetchall()]
