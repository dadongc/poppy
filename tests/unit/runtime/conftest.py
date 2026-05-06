from __future__ import annotations

import tempfile
from pathlib import Path

import pytest_asyncio

from src.infra.relational.sqlite import SqliteStore


@pytest_asyncio.fixture
async def sqlite_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    store = SqliteStore(path=tmp_path)
    await store.init()
    # Create runs tables
    await store.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            parent_run_id TEXT,
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',
            started_at REAL NOT NULL DEFAULT 0,
            finished_at REAL,
            error TEXT DEFAULT '',
            used_tokens INT NOT NULL DEFAULT 0,
            used_steps INT NOT NULL DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}'
        )
    """)
    await store.execute("""
        CREATE TABLE IF NOT EXISTS run_closure (
            ancestor TEXT NOT NULL,
            descendant TEXT NOT NULL,
            depth INT NOT NULL DEFAULT 0,
            PRIMARY KEY (ancestor, descendant)
        )
    """)
    yield store
    await store.close()
    Path(tmp_path).unlink(missing_ok=True)
