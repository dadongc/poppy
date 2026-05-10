from __future__ import annotations

import tempfile
from pathlib import Path

import pytest_asyncio

from src.infra.blob.filesystem import FilesystemBackend
from src.infra.cache.memory_cache import MemoryCache
from src.infra.eventbus.inproc import InProcessEventBus
from src.infra.keyword.fts5 import Fts5Index
from src.infra.relational.sqlite import SqliteStore
from src.infra.vector.sqlite_vec import SqliteVecIndex


@pytest_asyncio.fixture
async def sqlite_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    store = SqliteStore(path=tmp_path)
    await store.init()
    yield store
    await store.close()
    Path(tmp_path).unlink(missing_ok=True)


@pytest_asyncio.fixture
async def events_table(sqlite_store: SqliteStore):
    await sqlite_store.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            run_id TEXT NOT NULL,
            parent_run_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            user_id TEXT DEFAULT '',
            trace_id TEXT DEFAULT '',
            ts REAL NOT NULL,
            seq INTEGER NOT NULL,
            payload TEXT DEFAULT '{}',
            level TEXT DEFAULT 'info',
            scope TEXT DEFAULT 'public'
        )
    """)


@pytest_asyncio.fixture
async def event_bus(sqlite_store: SqliteStore, events_table: None):
    bus = InProcessEventBus(store=sqlite_store, persist=True)
    await bus.init()
    yield bus
    await bus.shutdown()


@pytest_asyncio.fixture
async def vec_index(sqlite_store: SqliteStore):
    idx = SqliteVecIndex(store=sqlite_store, dim=4)
    await idx.init()
    return idx


@pytest_asyncio.fixture
async def fts_index(sqlite_store: SqliteStore):
    idx = Fts5Index(store=sqlite_store)
    await idx.init()
    return idx


@pytest_asyncio.fixture
async def fs_backend():
    with tempfile.TemporaryDirectory() as tmp:
        fb = FilesystemBackend(root=tmp)
        await fb.init()
        yield fb


@pytest_asyncio.fixture
async def mem_cache():
    c = MemoryCache(max_size=100)
    await c.init()
    return c
