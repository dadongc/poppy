from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest_asyncio

from src.common.clock import now_ts
from src.common.ids import new_id
from src.infra.blob.filesystem import FilesystemBackend
from src.infra.cache.memory_cache import MemoryCache
from src.infra.eventbus.inproc import InProcessEventBus
from src.infra.keyword.fts5 import Fts5Index
from src.infra.relational.sqlite import SqliteStore
from src.infra.vector.sqlite_vec import SqliteVecIndex
from src.service.embedding.provider import StubEmbeddingProvider
from src.service.llm_protocol import StubLLM


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


@pytest_asyncio.fixture
async def event_bus():
    bus = InProcessEventBus(store=None, persist=False)
    await bus.init()
    yield bus
    await bus.shutdown()


@pytest_asyncio.fixture
async def stub_llm():
    return StubLLM()


@pytest_asyncio.fixture
async def stub_embedding():
    return StubEmbeddingProvider(dim=4)


# ---------------------------------------------------------------------------
# Table fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sessions_tables(sqlite_store: SqliteStore):
    await sqlite_store.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            created_at REAL NOT NULL,
            last_active_at REAL NOT NULL,
            message_count INTEGER DEFAULT 0,
            summary TEXT DEFAULT '',
            summary_covers_until_seq INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}'
        )
    """)
    await sqlite_store.execute("""
        CREATE TABLE IF NOT EXISTS session_messages (
            msg_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            run_id TEXT DEFAULT '',
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tool_calls TEXT DEFAULT '[]',
            tool_call_id TEXT DEFAULT '',
            name TEXT DEFAULT '',
            artifact_refs TEXT DEFAULT '[]',
            created_at REAL NOT NULL,
            metadata TEXT DEFAULT '{}',
            UNIQUE(session_id, seq)
        )
    """)


@pytest_asyncio.fixture
async def artifacts_tables(sqlite_store: SqliteStore):
    await sqlite_store.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            storage_uri TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            mime_type TEXT NOT NULL,
            encoding TEXT DEFAULT 'utf-8',
            summary TEXT DEFAULT '',
            preview TEXT,
            source_type TEXT NOT NULL,
            source_run_id TEXT,
            source_session_id TEXT,
            source_tool_name TEXT,
            source_call_id TEXT,
            created_at REAL NOT NULL,
            last_accessed_at REAL NOT NULL,
            access_count INTEGER DEFAULT 0,
            state TEXT DEFAULT 'active',
            expires_at REAL,
            pinned INTEGER DEFAULT 0,
            title TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            metadata TEXT DEFAULT '{}'
        )
    """)
    await sqlite_store.execute("""
        CREATE TABLE IF NOT EXISTS artifact_blob_refs (
            user_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            storage_uri TEXT NOT NULL,
            refcount INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, content_hash)
        )
    """)


@pytest_asyncio.fixture
async def kb_tables(sqlite_store: SqliteStore):
    await sqlite_store.execute("""
        CREATE TABLE IF NOT EXISTS kb_documents (
            doc_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            title TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_uri TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            state TEXT DEFAULT 'ingesting',
            chunk_count INTEGER DEFAULT 0,
            error TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            metadata TEXT DEFAULT '{}'
        )
    """)
    await sqlite_store.execute("""
        CREATE TABLE IF NOT EXISTS kb_chunks (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
            user_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            text TEXT NOT NULL,
            token_count INTEGER DEFAULT 0,
            embedding_model TEXT NOT NULL,
            char_start INTEGER DEFAULT 0,
            char_end INTEGER DEFAULT 0,
            heading_path TEXT DEFAULT '[]',
            metadata TEXT DEFAULT '{}'
        )
    """)


@pytest_asyncio.fixture
async def memory_tables(sqlite_store: SqliteStore):
    await sqlite_store.execute("""
        CREATE TABLE IF NOT EXISTS memory_records (
            memory_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_run_id TEXT,
            source_session_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_recalled_at REAL,
            occurred_at REAL,
            confidence REAL DEFAULT 1.0,
            importance REAL DEFAULT 0.5,
            recall_count INTEGER DEFAULT 0,
            state TEXT DEFAULT 'active',
            related_memory_ids TEXT DEFAULT '[]',
            artifact_refs TEXT DEFAULT '[]',
            tags TEXT DEFAULT '[]',
            metadata TEXT DEFAULT '{}'
        )
    """)


# ---------------------------------------------------------------------------
# JSON helpers for SQLite (JSONB fields stored as TEXT)
# ---------------------------------------------------------------------------


def _to_json(val):
    return json.dumps(val, ensure_ascii=False)


def _from_json(val):
    if val is None:
        return None
    if isinstance(val, str):
        return json.loads(val)
    return val
