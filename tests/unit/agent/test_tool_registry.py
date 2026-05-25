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
    async def test_list_for_agent_includes_builtins(self, tool_registry):
        """builtin 工具始终可见，即使不在 allowed_tools 中。"""
        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer"},
            token_budget=10000,
        )
        tools = tool_registry.list_for_agent(spec)
        names = {t.name for t in tools}
        assert "final_answer" in names
        # builtin 工具即使不在 allowed_tools 中也可见
        assert "forget" in names

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

    @pytest.mark.asyncio
    async def test_load_from_dir(self, tool_registry):
        """load_from_dir 直接将工具加载到 _tools。"""
        await tool_registry.load_from_dir("src/tools/custom")
        assert tool_registry.get("rss_fetch") is not None
        assert tool_registry.get("hackernews_top") is not None
        assert tool_registry.get("github_trending") is not None
        assert tool_registry.get("artifact_save") is not None

    @pytest.mark.asyncio
    async def test_list_for_agent_extra_tools(self, tool_registry):
        """extra_tools 中的 custom 工具也可见。"""
        await tool_registry.load_from_dir("src/tools/custom")

        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer"},
            token_budget=10000,
        )
        tools = tool_registry.list_for_agent(spec, extra_tools={"rss_fetch"})
        names = {t.name for t in tools}
        assert "final_answer" in names       # allowed
        assert "rss_fetch" in names          # extra_tools
        assert "forget" in names             # builtin 始终可见
        assert "hackernews_top" not in names  # 既不在 allowed 也不在 extra_tools

    @pytest.mark.asyncio
    async def test_list_for_agent_extra_tools_empty_allowed(self, tool_registry):
        """空 allowed_tools + extra_tools 时返回 builtin + extra。"""
        await tool_registry.load_from_dir("src/tools/custom")
        spec = AgentSpec(name="test", allowed_tools=set(), token_budget=10000)
        tools = tool_registry.list_for_agent(spec, extra_tools={"rss_fetch"})
        names = {t.name for t in tools}
        # builtin 全部 + rss_fetch
        assert "forget" in names
        assert "rss_fetch" in names
        # 其他 custom 不在 extra_tools 中，不可见
        assert "hackernews_top" not in names
