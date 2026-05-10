from __future__ import annotations

import asyncio

import pytest

from src.runtime.run_registry import RunRegistry


class TestRunRegistry:
    @pytest.mark.asyncio
    async def test_register_and_get(self, sqlite_store):
        reg = RunRegistry(sqlite_store)
        await reg.register("r1", "test-agent", session_id="s1", user_id="u1")
        info = await reg.get("r1")
        assert info is not None
        assert info.run_id == "r1"
        assert info.agent_name == "test-agent"
        assert info.state == "pending"

    @pytest.mark.asyncio
    async def test_get_missing(self, sqlite_store):
        reg = RunRegistry(sqlite_store)
        assert await reg.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_update_state(self, sqlite_store):
        reg = RunRegistry(sqlite_store)
        await reg.register("r1", "test-agent", session_id="s1", user_id="u1")
        await reg.update_state("r1", "running")
        info = await reg.get("r1")
        assert info.state == "running"
        await reg.update_state("r1", "completed", used_tokens=100, used_steps=5)
        info = await reg.get("r1")
        assert info.state == "completed"
        assert info.finished_at is not None
        assert info.used_tokens == 100
        assert info.used_steps == 5

    @pytest.mark.asyncio
    async def test_cancel_single(self, sqlite_store):
        reg = RunRegistry(sqlite_store)
        await reg.register("r1", "test-agent", session_id="s1", user_id="u1")
        count = await reg.cancel("r1")
        assert count == 1
        info = await reg.get("r1")
        assert info.state == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_cascade(self, sqlite_store):
        reg = RunRegistry(sqlite_store)
        await reg.register("parent", "test-agent", session_id="s1", user_id="u1")
        await reg.register("child1", "test-agent", session_id="s1", user_id="u1", parent_run_id="parent")
        await reg.register("child2", "test-agent", session_id="s1", user_id="u1", parent_run_id="parent")
        await reg.register("grandchild", "test-agent", session_id="s1", user_id="u1", parent_run_id="child1")

        count = await reg.cancel("parent")
        assert count == 4
        for rid in ("parent", "child1", "child2", "grandchild"):
            info = await reg.get(rid)
            assert info.state == "cancelled", f"{rid} should be cancelled"

    @pytest.mark.asyncio
    async def test_list_active(self, sqlite_store):
        reg = RunRegistry(sqlite_store)
        await reg.register("r1", "test-agent", session_id="s1", user_id="u1")
        await reg.register("r2", "test-agent", session_id="s1", user_id="u1")
        await reg.update_state("r1", "completed")

        active = await reg.list_active()
        assert len(active) == 1
        assert active[0].run_id == "r2"

    @pytest.mark.asyncio
    async def test_descendants(self, sqlite_store):
        reg = RunRegistry(sqlite_store)
        await reg.register("parent", "test-agent", session_id="s1", user_id="u1")
        await reg.register("child1", "test-agent", session_id="s1", user_id="u1", parent_run_id="parent")
        await reg.register("child2", "test-agent", session_id="s1", user_id="u1", parent_run_id="parent")

        desc = await reg.descendants("parent")
        assert set(desc) == {"child1", "child2"}

    @pytest.mark.asyncio
    async def test_attach_cancel_event(self, sqlite_store):
        reg = RunRegistry(sqlite_store)
        await reg.register("r1", "test-agent", session_id="s1", user_id="u1")
        ev = asyncio.Event()
        await reg.attach_cancel_event("r1", ev)
        # Cancel should set the event
        await reg.cancel("r1")
        assert ev.is_set()
