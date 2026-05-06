from __future__ import annotations

import pytest

from src.common.types import AgentSpec


class TestToolRegistry:
    @pytest.mark.asyncio
    async def test_load_builtins(self, tool_registry):
        assert tool_registry.get("final_answer") is not None
        assert tool_registry.get("delegate_task") is not None
        assert tool_registry.get("load_skill") is not None
        assert tool_registry.get("read_artifact") is not None
        assert tool_registry.get("remember") is not None
        assert tool_registry.get("forget") is not None

    @pytest.mark.asyncio
    async def test_get_missing(self, tool_registry):
        assert tool_registry.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_for_agent(self, tool_registry):
        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer", "remember"},
            token_budget=10000,
        )
        tools = tool_registry.list_for_agent(spec)
        names = {t.name for t in tools}
        assert "final_answer" in names
        assert "remember" in names
        assert "forget" not in names

    @pytest.mark.asyncio
    async def test_list_for_agent_empty_allowed(self, tool_registry):
        spec = AgentSpec(name="test", allowed_tools=set(), token_budget=10000)
        tools = tool_registry.list_for_agent(spec)
        assert len(tools) == len(tool_registry.list_all())

    @pytest.mark.asyncio
    async def test_tool_has_schema(self, tool_registry):
        fa = tool_registry.get("final_answer")
        assert fa is not None
        assert "properties" in fa.schema
        assert "answer" in fa.schema.get("required", [])
