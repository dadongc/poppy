from __future__ import annotations

import asyncio

import pytest

from src.agent.base_agent import BaseAgent
from src.common.clock import now_ts
from src.common.errors import BudgetExceededError
from src.common.ids import new_id
from src.common.types import (
    AgentContext,
    AgentSpec,
    LLMChunk,
    Message,
    Services,
)


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


class FakeOrch:
    async def spawn_subagent(self, **kwargs):
        return Message(role="assistant", content="sub done")


def _make_ctx(spec, services):
    return AgentContext(
        run_id=new_id("run"),
        session_id=new_id("ses"),
        user_id="u1",
        trace_id=new_id("trc"),
        spec=spec,
        user_message=Message(role="user", content="Hello", created_at=now_ts()),
        extra_inputs={"_orch": FakeOrch()},
        cancel_event=asyncio.Event(),
        deadline_at=now_ts() + 300,
        started_at=now_ts(),
        services=services,
    )


class TestReactFullLoop:
    @pytest.mark.asyncio
    async def test_basic_tool_call_loop(self, tool_registry):
        """Full ReAct loop: LLM returns final_answer tool call, agent executes it."""
        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer"},
            max_steps=5,
            token_budget=50000,
            deadline_sec=300,
            max_tokens=4096,
        )
        services = Services(tool=tool_registry, llm=StubLLMGateway())
        ctx = _make_ctx(spec, services)

        agent = BaseAgent(ctx=ctx, orchestrator=FakeOrch())
        result = await agent.run_loop()

        assert result.role == "assistant"
        assert result.content == "你好，世界"

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_text(self, tool_registry):
        """LLM returns plain text with no tool calls → treated as final answer."""
        spec = AgentSpec(
            name="test",
            allowed_tools=set(),
            max_steps=5,
            token_budget=50000,
            deadline_sec=300,
            max_tokens=4096,
        )
        llm = StubLLMGateway(chunks=[
            LLMChunk(type="text_delta", text="Hello there!"),
            LLMChunk(type="stop", stop_reason="end"),
        ])
        services = Services(tool=tool_registry, llm=llm)
        ctx = _make_ctx(spec, services)

        agent = BaseAgent(ctx=ctx, orchestrator=FakeOrch())
        result = await agent.run_loop()

        assert "Hello" in result.content

    @pytest.mark.asyncio
    async def test_multi_step_loop(self, tool_registry):
        """Multi-step: first call asks for remember, second calls final_answer."""
        step1 = [
            LLMChunk(type="text_delta", text="Let me remember."),
            LLMChunk(
                type="tool_call_start",
                tool_call_index=0,
                tool_call_id="tc1",
                tool_name="remember",
            ),
            LLMChunk(
                type="tool_call_delta",
                tool_call_index=0,
                arguments_delta='{"fact": "user likes AI", "kind": "preference"}',
            ),
            LLMChunk(
                type="tool_call_end",
                tool_call_index=0,
                tool_call_id="tc1",
                tool_name="remember",
                arguments_full={"fact": "user likes AI", "kind": "preference"},
            ),
            LLMChunk(type="stop", stop_reason="tool_calls"),
        ]
        step2 = [
            LLMChunk(type="text_delta", text="Remembered. Answering."),
            LLMChunk(
                type="tool_call_start",
                tool_call_index=0,
                tool_call_id="tc2",
                tool_name="final_answer",
            ),
            LLMChunk(
                type="tool_call_delta",
                tool_call_index=0,
                arguments_delta='{"answer": "已记住，你喜欢 AI"}',
            ),
            LLMChunk(
                type="tool_call_end",
                tool_call_index=0,
                tool_call_id="tc2",
                tool_name="final_answer",
                arguments_full={"answer": "已记住，你喜欢 AI"},
            ),
            LLMChunk(type="stop", stop_reason="tool_calls"),
        ]

        llm = MultiStepLLMGateway([step1, step2])
        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer", "remember"},
            max_steps=10,
            token_budget=50000,
            deadline_sec=300,
            max_tokens=4096,
        )
        services = Services(tool=tool_registry, llm=llm)
        ctx = _make_ctx(spec, services)

        agent = BaseAgent(ctx=ctx, orchestrator=FakeOrch())
        result = await agent.run_loop()

        assert result.content == "已记住，你喜欢 AI"
        assert llm.step == 2

    @pytest.mark.asyncio
    async def test_max_steps_exceeded(self, tool_registry):
        """Agent terminates when max steps reached."""
        # LLM keeps returning non-final tool calls
        infinite_step = [
            LLMChunk(type="text_delta", text="Processing."),
            LLMChunk(
                type="tool_call_start",
                tool_call_index=0,
                tool_call_id="tc_inf",
                tool_name="remember",
            ),
            LLMChunk(
                type="tool_call_delta",
                tool_call_index=0,
                arguments_delta='{"fact": "data", "kind": "note"}',
            ),
            LLMChunk(
                type="tool_call_end",
                tool_call_index=0,
                tool_call_id="tc_inf",
                tool_name="remember",
                arguments_full={"fact": "data", "kind": "note"},
            ),
            LLMChunk(type="stop", stop_reason="tool_calls"),
        ]

        llm = MultiStepLLMGateway([infinite_step, infinite_step, infinite_step])
        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer", "remember"},
            max_steps=2,
            token_budget=50000,
            deadline_sec=300,
            max_tokens=4096,
        )
        services = Services(tool=tool_registry, llm=llm)
        ctx = _make_ctx(spec, services)

        agent = BaseAgent(ctx=ctx, orchestrator=FakeOrch())
        with pytest.raises(BudgetExceededError):
            await agent.run_loop()

    @pytest.mark.asyncio
    async def test_cancel_during_stream(self, tool_registry):
        """Cancel set before streaming should raise CancelledError."""
        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer"},
            max_steps=5,
            token_budget=50000,
            deadline_sec=300,
            max_tokens=4096,
        )
        services = Services(tool=tool_registry, llm=StubLLMGateway())
        ctx = _make_ctx(spec, services)
        ctx.cancel_event.set()

        agent = BaseAgent(ctx=ctx, orchestrator=FakeOrch())
        from src.common.errors import CancelledError
        with pytest.raises(CancelledError):
            await agent.run_loop()

    @pytest.mark.asyncio
    async def test_deadline_passed(self, tool_registry):
        """Deadline in the past should raise TimeoutError."""
        spec = AgentSpec(
            name="test",
            allowed_tools={"final_answer"},
            max_steps=5,
            token_budget=50000,
            deadline_sec=300,
            max_tokens=4096,
        )
        services = Services(tool=tool_registry, llm=StubLLMGateway())
        ctx = _make_ctx(spec, services)
        ctx.deadline_at = now_ts() - 1

        agent = BaseAgent(ctx=ctx, orchestrator=FakeOrch())
        from src.common.errors import TimeoutError
        with pytest.raises(TimeoutError):
            await agent.run_loop()
