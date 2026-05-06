from __future__ import annotations

import tempfile
from pathlib import Path

import pytest_asyncio

from src.common.types import (
    AgentSpec,
    LLMChunk,
)
from src.infra.eventbus.inproc import InProcessEventBus
from src.infra.relational.sqlite import SqliteStore
from src.service.session import SessionService
from src.tools.registry import ToolRegistry


class StubLLMGateway:
    """LLM gateway that emits a final_answer tool call then stops."""

    def __init__(self, chunks=None):
        self._chunks = chunks
        self.call_count = 0

    async def stream(self, payload, ctx):
        self.call_count += 1
        chunks = self._chunks or [
            LLMChunk(type="text_delta", text="Let me answer."),
            LLMChunk(
                type="tool_call_start",
                tool_call_index=0,
                tool_call_id="tc1",
                tool_name="final_answer",
            ),
            LLMChunk(
                type="tool_call_delta",
                tool_call_index=0,
                arguments_delta='{"answer": "你好，世界"}',
            ),
            LLMChunk(
                type="tool_call_end",
                tool_call_index=0,
                tool_call_id="tc1",
                tool_name="final_answer",
                arguments_full={"answer": "你好，世界"},
            ),
            LLMChunk(type="stop", stop_reason="tool_calls"),
        ]
        for c in chunks:
            yield c


class MultiStepLLMGateway:
    """LLM gateway for multi-step tests."""

    def __init__(self, step_chunks):
        self._step_chunks = step_chunks
        self.step = 0

    async def stream(self, payload, ctx):
        if self.step < len(self._step_chunks):
            chunks = self._step_chunks[self.step]
        else:
            chunks = [
                LLMChunk(type="text_delta", text="Done."),
                LLMChunk(type="stop", stop_reason="end"),
            ]
        self.step += 1
        for c in chunks:
            yield c


@pytest_asyncio.fixture
async def sqlite_store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_path = f.name
    store = SqliteStore(path=tmp_path)
    await store.init()
    # Create sessions table for session service
    await store.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            messages TEXT NOT NULL DEFAULT '[]',
            summary TEXT DEFAULT '',
            created_at REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (session_id, user_id)
        )
    """)
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


@pytest_asyncio.fixture
async def event_bus(sqlite_store):
    bus = InProcessEventBus(store=sqlite_store, persist=False)
    await bus.init()
    yield bus
    await bus.shutdown()


@pytest_asyncio.fixture
async def tool_registry():
    reg = ToolRegistry()
    await reg.load_builtins()
    return reg


@pytest_asyncio.fixture
async def session_service(sqlite_store):
    svc = SessionService(store=sqlite_store)
    return svc


@pytest_asyncio.fixture
def spec():
    return AgentSpec(
        name="integration-test",
        description="Integration test agent",
        preferred_model="stub",
        temperature=0.7,
        max_tokens=4096,
        allowed_tools={"final_answer"},
        max_steps=5,
        token_budget=50000,
        deadline_sec=300,
        max_parallel_tools=3,
    )
