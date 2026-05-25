from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest_asyncio

from src.common.clock import now_ts
from src.common.ids import new_id
from src.common.types import (
    AgentContext,
    AgentSpec,
    Message,
    Services,
)
from src.infra.relational.sqlite import SqliteStore
from src.service.artifact import ArtifactStore


class FakeBlobBackend:
    """Stub blob 后端，所有内容存内存。"""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def init(self) -> None:
        pass

    async def put(self, key: str, data: bytes, mime_type: str = "") -> str:
        self._data[key] = data
        return f"fake://{key}"

    async def get(self, key: str) -> bytes | None:
        return self._data.get(key)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self._data

    async def close(self) -> None:
        self._data.clear()


class FakeEventBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def init(self) -> None:
        pass

    async def publish(self, event: Any) -> None:
        self.events.append(event)

    def subscribe(self, filter: dict | None = None) -> Any:
        raise NotImplementedError

    async def replay(self, run_id: str, since_seq: int = 0) -> Any:
        raise NotImplementedError

    async def shutdown(self, timeout: float = 30.0) -> None:
        pass


@dataclass(slots=True, kw_only=True)
class FakeArtifact:
    artifact_id: str
    user_id: str = ""
    mime_type: str = ""
    title: str = ""
    size_bytes: int = 0


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
async def fake_artifact_store(sqlite_store):
    """创建完整的 ArtifactStore，走 SQLite + FakeBlob + FakeEventBus。"""
    blob = FakeBlobBackend()
    await blob.init()
    bus = FakeEventBus()
    store = ArtifactStore(store=sqlite_store, blob=blob, event_bus=bus)
    await store.init()
    return store


@pytest_asyncio.fixture
def spec():
    return AgentSpec(
        name="test-agent",
        allowed_tools={
            "artifact_save", "rss_fetch", "hackernews_top", "github_trending",
        },
        token_budget=10000,
    )


@pytest_asyncio.fixture
def agent_ctx_no_svc(spec):
    """不含任何 service 的空上下文，用于测试 service missing 路径。"""
    return AgentContext(
        run_id=new_id("run"),
        session_id=new_id("ses"),
        user_id="test-user",
        spec=spec,
        user_message=Message(role="user", content="test", created_at=now_ts()),
        cancel_event=asyncio.Event(),
        deadline_at=now_ts() + 300,
        started_at=now_ts(),
        services=Services(),
    )


@pytest_asyncio.fixture
def agent_ctx_with_artifact(spec, fake_artifact_store):
    """含 artifact 服务的上下文。"""
    return AgentContext(
        run_id=new_id("run"),
        session_id=new_id("ses"),
        user_id="test-user",
        spec=spec,
        user_message=Message(role="user", content="test", created_at=now_ts()),
        cancel_event=asyncio.Event(),
        deadline_at=now_ts() + 300,
        started_at=now_ts(),
        services=Services(artifact=fake_artifact_store),
    )
