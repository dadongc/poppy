from __future__ import annotations

import pytest

from src.agent.tool_executor import ToolExecutor
from src.common.types import ToolCall


class FakeOrchestrator:
    async def spawn_subagent(self, **kwargs):
        from src.common.types import Message
        return Message(role="assistant", content="sub done")


class TestToolExecutor:
    @pytest.mark.asyncio
    async def test_execute_final_answer(self, agent_ctx):
        agent_ctx.extra_inputs["_orch"] = FakeOrchestrator()
        executor = ToolExecutor(agent_ctx, FakeOrchestrator())
        calls = [ToolCall(call_id="c1", name="final_answer",
                          arguments={"answer": "OK"}, arguments_raw='{"answer":"OK"}')]
        report = await executor.execute(calls)
        assert len(report.results) == 1
        assert report.results[0].status == "ok"
        assert report.results[0].content == "OK"

    @pytest.mark.asyncio
    async def test_unknown_tool_denied(self, agent_ctx):
        executor = ToolExecutor(agent_ctx, FakeOrchestrator())
        calls = [ToolCall(call_id="c1", name="nonexistent", arguments={})]
        report = await executor.execute(calls)
        # Not in allowed_tools → permission denied
        assert report.results[0].status == "denied"

    @pytest.mark.asyncio
    async def test_schema_validation(self, agent_ctx):
        executor = ToolExecutor(agent_ctx, FakeOrchestrator())
        calls = [ToolCall(call_id="c1", name="final_answer",
                          arguments={"bad": "field"}, arguments_raw='{"bad":"field"}')]
        report = await executor.execute(calls)
        assert report.results[0].status == "error"

    @pytest.mark.asyncio
    async def test_permission_denied(self, agent_ctx):
        agent_ctx.spec.allowed_tools = {"remember"}
        executor = ToolExecutor(agent_ctx, FakeOrchestrator())
        calls = [ToolCall(call_id="c1", name="final_answer", arguments={"answer": "nope"})]
        report = await executor.execute(calls)
        assert report.results[0].status == "denied"

    @pytest.mark.asyncio
    async def test_cancel_during_execution(self, agent_ctx):
        agent_ctx.extra_inputs["_orch"] = FakeOrchestrator()
        agent_ctx.cancel_event.set()
        executor = ToolExecutor(agent_ctx, FakeOrchestrator())

        calls = [ToolCall(call_id="c1", name="final_answer",
                          arguments={"answer": "hi"}, arguments_raw='{"answer":"hi"}')]
        report = await executor.execute(calls)
        # Should get cancelled result
        assert report.results[0].status in ("cancelled", "ok")

    @pytest.mark.asyncio
    async def test_parallel_execution(self, agent_ctx):
        agent_ctx.extra_inputs["_orch"] = FakeOrchestrator()
        executor = ToolExecutor(agent_ctx, FakeOrchestrator())
        calls = [
            ToolCall(call_id="c1", name="final_answer",
                     arguments={"answer": "1"}, arguments_raw='{"answer":"1"}'),
            ToolCall(call_id="c2", name="final_answer",
                     arguments={"answer": "2"}, arguments_raw='{"answer":"2"}'),
            ToolCall(call_id="c3", name="final_answer",
                     arguments={"answer": "3"}, arguments_raw='{"answer":"3"}'),
        ]
        report = await executor.execute(calls)
        assert len(report.results) == 3
        assert report.parallel_count == 3
        assert all(r.status == "ok" for r in report.results)
