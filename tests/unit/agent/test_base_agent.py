from __future__ import annotations

import asyncio

import pytest

from src.agent.base_agent import BaseAgent
from src.common.clock import now_ts
from src.common.errors import BudgetExceededError, CancelledError, TimeoutError
from src.common.ids import new_id
from src.common.types import (
    AgentContext,
    AgentSpec,
    LLMChunk,
    Message,
    PromptPayload,
    Services,
)


class StubLLMGateway:
    """Stub LLM gateway that returns a final_answer tool call."""

    def __init__(self, responses: list[list[LLMChunk]] | None = None):
        self.responses = responses or []
        self.call_count = 0

    async def stream(self, payload: PromptPayload, ctx: AgentContext):
        if self.call_count < len(self.responses):
            chunks = self.responses[self.call_count]
        else:
            chunks = [
                LLMChunk(type="text_delta", text="I'll answer."),
                LLMChunk(
                    type="tool_call_start",
                    tool_call_index=0,
                    tool_call_id="tc1",
                    tool_name="final_answer",
                ),
                LLMChunk(type="tool_call_delta", tool_call_index=0,
                         arguments_delta='{"answer": "done"}'),
                LLMChunk(
                    type="tool_call_end",
                    tool_call_index=0,
                    tool_call_id="tc1",
                    tool_name="final_answer",
                    arguments_full={"answer": "done"},
                ),
                LLMChunk(type="stop", stop_reason="tool_calls"),
            ]
        self.call_count += 1
        for c in chunks:
            yield c


class FakeOrch:
    async def spawn_subagent(self, **kwargs):
        return Message(role="assistant", content="sub done")


class TestBaseAgent:
    @pytest.mark.asyncio
    async def test_terminates_on_final_answer(self, tool_registry):
        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer"},
            max_steps=5,
            token_budget=100000,
            deadline_sec=300,
        )
        services = Services(tool=tool_registry)
        ctx = AgentContext(
            run_id=new_id("run"),
            user_id="u1",
            spec=spec,
            user_message=Message(role="user", content="hi", created_at=now_ts()),
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + 300,
            started_at=now_ts(),
            services=services,
        )
        services.llm = StubLLMGateway()
        ctx.extra_inputs["_orch"] = FakeOrch()

        agent = BaseAgent(ctx=ctx, orchestrator=FakeOrch())
        result = await agent.run_loop()
        assert result.role == "assistant"
        assert result.content == "done"

    @pytest.mark.asyncio
    async def test_terminates_on_max_steps(self, tool_registry):
        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer"},
            max_steps=0,
            token_budget=100000,
            deadline_sec=300,
        )
        services = Services(tool=tool_registry)
        ctx = AgentContext(
            run_id=new_id("run"),
            user_id="u1",
            spec=spec,
            user_message=Message(role="user", content="hi", created_at=now_ts()),
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + 300,
            started_at=now_ts(),
            services=services,
        )
        services.llm = StubLLMGateway()
        ctx.extra_inputs["_orch"] = FakeOrch()

        agent = BaseAgent(ctx=ctx, orchestrator=FakeOrch())
        with pytest.raises(BudgetExceededError):
            await agent.run_loop()

    @pytest.mark.asyncio
    async def test_terminates_on_cancel(self, tool_registry):
        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer"},
            max_steps=10,
            token_budget=100000,
            deadline_sec=300,
        )
        services = Services(tool=tool_registry)
        ctx = AgentContext(
            run_id=new_id("run"),
            user_id="u1",
            spec=spec,
            user_message=Message(role="user", content="hi", created_at=now_ts()),
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + 300,
            started_at=now_ts(),
            services=services,
        )
        ctx.cancel_event.set()
        services.llm = StubLLMGateway()

        agent = BaseAgent(ctx=ctx, orchestrator=FakeOrch())
        with pytest.raises(CancelledError):
            await agent.run_loop()

    @pytest.mark.asyncio
    async def test_terminates_on_deadline(self, tool_registry):
        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer"},
            max_steps=10,
            token_budget=100000,
            deadline_sec=300,
        )
        services = Services(tool=tool_registry)
        ctx = AgentContext(
            run_id=new_id("run"),
            user_id="u1",
            spec=spec,
            user_message=Message(role="user", content="hi", created_at=now_ts()),
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() - 1,  # Already passed
            started_at=now_ts(),
            services=services,
        )
        services.llm = StubLLMGateway()

        agent = BaseAgent(ctx=ctx, orchestrator=FakeOrch())
        with pytest.raises(TimeoutError):
            await agent.run_loop()

    @pytest.mark.asyncio
    async def test_no_tool_calls_is_final_answer(self, tool_registry):
        spec = AgentSpec(
            name="test",
            allowed_tools=set(),
            max_steps=5,
            token_budget=100000,
            deadline_sec=300,
        )
        services = Services(tool=tool_registry)
        ctx = AgentContext(
            run_id=new_id("run"),
            user_id="u1",
            spec=spec,
            user_message=Message(role="user", content="hi", created_at=now_ts()),
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + 300,
            started_at=now_ts(),
            services=services,
        )

        # LLM returns no tool calls
        class NoToolLLM:
            async def stream(self, payload, ctx):
                yield LLMChunk(type="text_delta", text="Hello there!")
                yield LLMChunk(type="stop", stop_reason="end")

        services.llm = NoToolLLM()

        agent = BaseAgent(ctx=ctx, orchestrator=FakeOrch())
        result = await agent.run_loop()
        assert result.role == "assistant"
        assert "Hello" in result.content
