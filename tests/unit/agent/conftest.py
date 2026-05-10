from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest_asyncio

from src.agent.llm_circuit_breaker import CircuitBreaker
from src.common.clock import now_ts
from src.common.ids import new_id
from src.common.types import (
    AgentContext,
    AgentSpec,
    Message,
    Services,
)
from src.infra.eventbus.inproc import InProcessEventBus
from src.infra.relational.sqlite import SqliteStore
from src.tools.registry import ToolRegistry


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
async def event_bus():
    bus = InProcessEventBus(store=None, persist=False)
    await bus.init()
    yield bus
    await bus.shutdown()


@pytest_asyncio.fixture
async def tool_registry():
    reg = ToolRegistry()
    await reg.load_builtins()
    return reg


@pytest_asyncio.fixture
def spec():
    return AgentSpec(
        name="test-agent",
        description="Test agent",
        preferred_model="deepseek-chat",
        fallback_models=["gpt-4o-mini"],
        temperature=0.7,
        max_tokens=4096,
        allowed_tools={
            "final_answer", "delegate_task", "load_skill",
            "read_artifact", "remember", "forget",
        },
        max_steps=10,
        token_budget=50000,
        deadline_sec=300,
        max_parallel_tools=3,
    )


@pytest_asyncio.fixture
async def agent_ctx(spec, tool_registry, event_bus):
    services = Services(
        tool=tool_registry,
        event_bus=event_bus,
    )
    return AgentContext(
        run_id=new_id("run"),
        session_id=new_id("ses"),
        user_id="test-user",
        trace_id=new_id("trc"),
        spec=spec,
        user_message=Message(role="user", content="Hello", created_at=now_ts()),
        cancel_event=asyncio.Event(),
        deadline_at=now_ts() + 300,
        started_at=now_ts(),
        services=services,
    )


@pytest_asyncio.fixture
def circuit_breaker():
    return CircuitBreaker(failure_threshold=3, cooldown_sec=5.0, window_sec=60.0)
