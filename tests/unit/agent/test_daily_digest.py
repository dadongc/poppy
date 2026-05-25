from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from src.agent.tool_executor import ToolExecutor
from src.common.clock import now_ts
from src.common.ids import new_id
from src.common.types import AgentContext, AgentSpec, Message, Services, ToolCall
from src.tools.builtin.load_skill import LoadSkillTool
from src.tools.registry import ToolRegistry


class FakeOrch:
    async def spawn_subagent(self, **kw):
        return Message(role="assistant", content="done")


@pytest_asyncio.fixture
async def daily_digest_registry():
    """加载 builtin + custom 工具。"""
    reg = ToolRegistry()
    await reg.load_builtins()
    await reg.load_from_dir("src/tools/custom")
    return reg


@pytest_asyncio.fixture
async def daily_digest_skills():
    from src.skills.registry import SkillRegistry

    reg = SkillRegistry(
        skills_path="src/skills",
        user_skills_path="src/skills-user",
    )
    await reg.load()
    return reg


@pytest_asyncio.fixture
def daily_digest_spec():
    return AgentSpec(
        name="daily-digest",
        allowed_tools={
            "load_skill", "final_answer", "web_fetch", "web_search",
        },
        token_budget=32000,
        max_steps=15,
    )


@pytest_asyncio.fixture
def daily_digest_ctx(daily_digest_registry, daily_digest_skills, daily_digest_spec):
    return AgentContext(
        run_id=new_id("run"),
        session_id=new_id("ses"),
        user_id="test-user",
        spec=daily_digest_spec,
        user_message=Message(role="user", content="生成今日技术日报", created_at=now_ts()),
        cancel_event=asyncio.Event(),
        deadline_at=now_ts() + 300,
        started_at=now_ts(),
        services=Services(
            tool=daily_digest_registry,
            skill=daily_digest_skills,
        ),
    )


class TestDailyDigestSkillIntegration:
    @pytest.mark.asyncio
    async def test_skill_exists(self, daily_digest_skills):
        """daily-digest skill 已加载。"""
        sk = await daily_digest_skills.get("daily-digest")
        assert sk is not None
        assert sk.name == "daily-digest"
        assert "rss_fetch" in sk.required_tools + sk.optional_tools
        assert "hackernews_top" in sk.required_tools + sk.optional_tools
        assert "github_trending" in sk.required_tools + sk.optional_tools
        assert "artifact_save" in sk.required_tools + sk.optional_tools

    @pytest.mark.asyncio
    async def test_custom_tools_loaded_but_not_active(self, daily_digest_registry, daily_digest_spec):
        """custom 工具在 registry 中，但不在 allowed_tools 时不可见（内置工具除外）。"""
        assert daily_digest_registry.get("rss_fetch") is not None
        tools = daily_digest_registry.list_for_agent(daily_digest_spec)
        names = {t.name for t in tools}
        # builtin 常量可见
        assert "load_skill" in names
        assert "final_answer" in names
        # custom 不在 allowed_tools 中，不可见
        assert "rss_fetch" not in names
        assert "artifact_save" not in names

    @pytest.mark.asyncio
    async def test_load_skill_activates_custom_tools(self, daily_digest_ctx):
        """load_skill daily-digest 后，custom tools 出现在 active_custom_tools。"""
        tool = LoadSkillTool()
        result = await tool.execute(daily_digest_ctx, {"skill_name": "daily-digest"})
        assert result.status == "ok"

        active = daily_digest_ctx.extra_inputs.get("active_custom_tools", set())
        assert "rss_fetch" in active
        assert "hackernews_top" in active
        assert "github_trending" in active
        assert "artifact_save" in active

    @pytest.mark.asyncio
    async def test_list_for_agent_after_skill_load(self, daily_digest_ctx):
        """load_skill 后，list_for_agent 包含 custom tools。"""
        tool = LoadSkillTool()
        await tool.execute(daily_digest_ctx, {"skill_name": "daily-digest"})

        extra = daily_digest_ctx.extra_inputs.get("active_custom_tools", set())
        reg = daily_digest_ctx.services.tool
        tools = reg.list_for_agent(daily_digest_ctx.spec, extra_tools=extra)
        names = {t.name for t in tools}

        assert "rss_fetch" in names
        assert "hackernews_top" in names
        assert "github_trending" in names
        assert "artifact_save" in names
        # builtin 始终在
        assert "load_skill" in names

    @pytest.mark.asyncio
    async def test_permission_allows_activated_custom_tool(self, daily_digest_ctx):
        """loaded skill 后，custom tool 可以通过权限检查。"""
        tool = LoadSkillTool()
        await tool.execute(daily_digest_ctx, {"skill_name": "daily-digest"})

        executor = ToolExecutor(daily_digest_ctx, FakeOrch())
        calls = [ToolCall(call_id="c1", name="rss_fetch", arguments={"urls": ["https://example.com/rss"]})]
        report = await executor.execute(calls)

        # rss_fetch 会尝试网络请求，可能成功也可能失败（网络错误），但不应是 denied
        assert report.results[0].status != "denied"

    @pytest.mark.asyncio
    async def test_permission_denies_unactivated_custom_tool(self, daily_digest_ctx):
        """未激活的 custom tool 仍会被拒绝。"""
        executor = ToolExecutor(daily_digest_ctx, FakeOrch())
        calls = [ToolCall(call_id="c1", name="rss_fetch", arguments={"urls": ["https://example.com/rss"]})]
        report = await executor.execute(calls)

        # 没有 load_skill，rss_fetch 不在 allowed_tools 也不在 active_custom_tools
        assert report.results[0].status == "denied"

    @pytest.mark.asyncio
    async def test_skill_loaded_skills_list(self, daily_digest_ctx):
        """loaded_skills 正确记录。"""
        tool = LoadSkillTool()
        await tool.execute(daily_digest_ctx, {"skill_name": "daily-digest"})

        loaded = daily_digest_ctx.extra_inputs.get("loaded_skills", [])
        assert len(loaded) == 1
        assert loaded[0].name == "daily-digest"
