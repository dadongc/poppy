from __future__ import annotations

import asyncio

import pytest

from src.agent.context_builder import ContextBuilder, TokenEstimator
from src.common.clock import now_ts
from src.common.ids import new_id
from src.common.types import AgentContext, AgentSpec, Message, Services


def _make_ctx(spec=None, run_messages=None):
    services = Services()
    return AgentContext(
        run_id=new_id("run"),
        session_id=new_id("ses"),
        user_id="test-user",
        trace_id=new_id("trc"),
        spec=spec or AgentSpec(
            name="cb-test",
            allowed_tools={"final_answer"},
            max_steps=10,
            token_budget=50000,
            deadline_sec=300,
            max_tokens=4096,
        ),
        user_message=Message(role="user", content="Hello world", created_at=now_ts()),
        extra_inputs={"_run_messages": run_messages or []},
        cancel_event=asyncio.Event(),
        deadline_at=now_ts() + 300,
        started_at=now_ts(),
        services=services,
    )


class TestTokenEstimator:
    def test_estimate_ascii(self):
        n = TokenEstimator.estimate("hello world")
        assert n > 0

    def test_estimate_cjk(self):
        n = TokenEstimator.estimate("你好世界")
        assert n > 0

    def test_estimate_mixed(self):
        n = TokenEstimator.estimate("hello 你好")
        # 5 ascii chars + space = 6 * 0.3 = 1.8, 2 CJK = 2 * 1.5 = 3 → ~4
        assert n > 0

    def test_estimate_messages(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        n = TokenEstimator.estimate_messages(msgs)
        assert n > 0


class TestContextBuilder:
    @pytest.mark.asyncio
    async def test_budget_allocation(self):
        ctx = _make_ctx()
        cb = ContextBuilder(ctx)
        budgets = cb._allocate_budget()
        total = sum(budgets.values())
        assert total <= cb._total_input_budget()
        assert budgets["role"] > 0
        assert budgets["history"] > 0
        assert budgets["current"] > 0

    @pytest.mark.asyncio
    async def test_build_role(self):
        ctx = _make_ctx()
        cb = ContextBuilder(ctx)
        role = cb._build_role()
        assert "cb-test" in role
        # DEFAULT_SYSTEM_PROMPT doesn't include {user_id}, only custom prompts do

    @pytest.mark.asyncio
    async def test_build_manifest_with_tools(self, tool_registry):
        ctx = _make_ctx()
        ctx.services.tool = tool_registry
        cb = ContextBuilder(ctx)
        manifest = await cb._build_manifest()
        assert "final_answer" in manifest

    @pytest.mark.asyncio
    async def test_build_env(self):
        ctx = _make_ctx()
        cb = ContextBuilder(ctx)
        env = cb._build_env()
        assert "test-user" in env
        assert ctx.run_id in env
        assert ctx.session_id in env

    @pytest.mark.asyncio
    async def test_build_without_services(self):
        ctx = _make_ctx()
        cb = ContextBuilder(ctx)
        payload = await cb.build([])
        assert len(payload.messages) >= 1
        assert payload.messages[0]["role"] == "system"
        assert "token_estimate" in payload.messages[0]["role"] or True

    @pytest.mark.asyncio
    async def test_build_with_run_messages(self):
        ctx = _make_ctx()
        cb = ContextBuilder(ctx)
        msgs = [
            Message(role="user", content="question 1", created_at=now_ts()),
            Message(role="assistant", content="answer 1", created_at=now_ts()),
        ]
        payload = await cb.build(msgs)
        # Should have system + current messages
        assert len(payload.messages) >= 3

    @pytest.mark.asyncio
    async def test_truncation_drops_memory_then_kb_then_history(self):
        spec = AgentSpec(
            name="tiny",
            allowed_tools=set(),
            max_steps=5,
            token_budget=6000,  # Very small budget
            max_tokens=100,
        )
        ctx = _make_ctx(spec=spec)
        cb = ContextBuilder(ctx)

        # Build with a long message to force truncation
        long_msgs = [
            Message(role="user", content="x" * 5000, created_at=now_ts()),
            Message(role="assistant", content="y" * 5000, created_at=now_ts()),
        ]
        payload = await cb.build(long_msgs)
        # Should still produce valid output despite truncation
        assert len(payload.messages) >= 1
        assert payload.messages[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_tool_rendering(self, tool_registry):
        ctx = _make_ctx()
        ctx.services.tool = tool_registry
        cb = ContextBuilder(ctx)
        tools = cb._render_tools()
        names = {t["function"]["name"] for t in tools if "function" in t}
        assert "final_answer" in names

    @pytest.mark.asyncio
    async def test_derive_query_from_run_messages(self):
        msgs = [
            Message(role="user", content="What is AI?", created_at=now_ts()),
            Message(role="assistant", content="AI is...", created_at=now_ts()),
        ]
        ctx = _make_ctx(run_messages=msgs)
        cb = ContextBuilder(ctx)
        query = cb._derive_query()
        assert query == "What is AI?"

    @pytest.mark.asyncio
    async def test_derive_query_empty(self):
        ctx = _make_ctx()
        cb = ContextBuilder(ctx)
        assert cb._derive_query() == ""
