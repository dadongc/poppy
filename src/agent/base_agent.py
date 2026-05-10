from __future__ import annotations

import json
from typing import TYPE_CHECKING

from src.common.clock import now_ts
from src.common.errors import BudgetExceededError, CancelledError, TimeoutError
from src.common.ids import EVENT_ID
from src.common.types import Event, EventType, Message, ToolCall

from .context_builder import ContextBuilder
from .tool_executor import ToolExecutor

if TYPE_CHECKING:
    from src.agent.llm_gateway import LLMGateway
    from src.common.types import AgentContext

    from .orchestrator import Orchestrator


class BaseAgent:
    """ReAct loop 核心。perceive → plan → act → observe 循环。"""

    def __init__(self, *, ctx: AgentContext, orchestrator: Orchestrator) -> None:
        self.ctx = ctx
        self.orch = orchestrator
        self.run_messages: list[Message] = []
        self._final_message: Message | None = None

    async def run_loop(self) -> Message:
        ctx = self.ctx

        if ctx.user_message:
            self.run_messages.append(ctx.user_message)

        builder = ContextBuilder(ctx)

        while True:
            self._check_termination()

            # Build prompt — override derive query for this loop
            builder._derive_query = lambda: self._last_user_query()  # type: ignore[method-assign]
            payload = await builder.build(self.run_messages)

            # Stream LLM
            assistant_msg, tool_calls = await self._stream_llm(payload)
            self.run_messages.append(assistant_msg)
            ctx.used_steps += 1

            # Check for termination via final_answer
            final_call = next(
                (tc for tc in tool_calls if tc.name == "final_answer"), None
            )
            if final_call:
                answer = final_call.arguments.get("answer", "")
                self._final_message = Message(
                    role="assistant",
                    content=answer,
                    metadata={"final_answer_args": final_call.arguments},
                    created_at=now_ts(),
                )
                self.run_messages.append(self._final_message)
                return self._final_message

            # No tool calls → final answer
            if not tool_calls:
                self._final_message = assistant_msg
                return assistant_msg

            # Execute tools
            executor = ToolExecutor(ctx, self.orch)
            report = await executor.execute(tool_calls)

            # Inject tool results
            for r in report.results:
                tool_msg = Message(
                    role="tool",
                    tool_call_id=r.call_id,
                    name=r.name,
                    content=r.content,
                    metadata={
                        "status": r.status,
                        "artifact_id": r.artifact_id or "",
                    },
                    created_at=now_ts(),
                )
                self.run_messages.append(tool_msg)

    def _check_termination(self) -> None:
        ctx = self.ctx
        if ctx.cancel_event.is_set():
            raise CancelledError(f"run {ctx.run_id} cancelled")
        if ctx.deadline_at > 0 and now_ts() > ctx.deadline_at:
            raise TimeoutError(f"run {ctx.run_id} deadline reached")
        if ctx.spec:
            if ctx.used_tokens > ctx.spec.token_budget:
                raise BudgetExceededError("token budget exceeded")
            if ctx.used_steps >= ctx.spec.max_steps:
                raise BudgetExceededError("max steps reached")

    async def _stream_llm(
        self, payload
    ) -> tuple[Message, list[ToolCall]]:
        ctx = self.ctx
        bus = ctx.services.event_bus
        llm: LLMGateway = ctx.services.llm  # type: ignore[assignment]

        text_parts: list[str] = []
        tool_calls_buf: dict[int, ToolCall] = {}
        errors: list[str] = []

        async for chunk in llm.stream(payload, ctx):
            if chunk.type == "text_delta":
                text_parts.append(chunk.text)
                if bus:
                    await bus.publish(Event(
                        event_id=EVENT_ID(),
                        type=EventType.LLM_TEXT_DELTA,
                        run_id=ctx.run_id,
                        parent_run_id=ctx.parent_run_id,
                        session_id=ctx.session_id,
                        user_id=ctx.user_id,
                        ts=now_ts(),
                        payload={"text": chunk.text},
                    ))
            elif chunk.type == "tool_call_start":
                tc = ToolCall(
                    call_id=chunk.tool_call_id,
                    name=chunk.tool_name,
                    arguments={},
                    arguments_raw="",
                )
                tool_calls_buf[chunk.tool_call_index] = tc
                if bus:
                    await bus.publish(Event(
                        event_id=EVENT_ID(),
                        type=EventType.LLM_TOOL_CALL_START,
                        run_id=ctx.run_id,
                        session_id=ctx.session_id,
                        user_id=ctx.user_id,
                        ts=now_ts(),
                        payload={"call_id": chunk.tool_call_id, "name": chunk.tool_name},
                    ))
            elif chunk.type == "tool_call_delta":
                if chunk.tool_call_index in tool_calls_buf:
                    tool_calls_buf[chunk.tool_call_index].arguments_raw += chunk.arguments_delta
            elif chunk.type == "tool_call_end":
                if chunk.tool_call_index in tool_calls_buf:
                    tc = tool_calls_buf[chunk.tool_call_index]
                    tc.arguments = chunk.arguments_full or _parse_json(tc.arguments_raw)
            elif chunk.type == "usage" and chunk.usage:
                ctx.used_tokens += chunk.usage.total_tokens
            elif chunk.type == "error":
                err = chunk.error
                if err:
                    errors.append(f"[{err.type}] {err.message}")
                if bus:
                    await bus.publish(Event(
                        event_id=EVENT_ID(),
                        type=EventType.LLM_ERROR,
                        run_id=ctx.run_id,
                        session_id=ctx.session_id,
                        user_id=ctx.user_id,
                        ts=now_ts(),
                        payload={"error_type": err.type if err else "unknown",
                                 "message": err.message if err else ""},
                    ))
            elif chunk.type == "stop":
                pass

        full_text = "".join(text_parts)
        if errors and not full_text and not tool_calls_buf:
            full_text = f"调用模型时发生错误: {'; '.join(errors)}"
        tcs = sorted(tool_calls_buf.values(), key=lambda x: x.call_id)
        msg = Message(
            role="assistant",
            content=full_text,
            tool_calls=tcs,
            created_at=now_ts(),
        )
        return msg, tcs

    def _last_user_query(self) -> str:
        for m in reversed(self.run_messages):
            if m.role == "user":
                return m.content
        return ""


def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw}
