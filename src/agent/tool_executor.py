from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any

import jsonschema

from src.common.clock import now_ts
from src.common.errors import NotFoundError, PermissionDeniedError
from src.common.ids import EVENT_ID
from src.common.types import Event, EventType, ExecutionReport, ToolCall, ToolResult

if TYPE_CHECKING:
    from src.agent.orchestrator import Orchestrator
    from src.common.types import AgentContext
    from src.tools.protocol import Tool

LLM_INJECT_LIMIT = 4000


class SchemaValidationError(Exception):
    pass


class ToolExecutor:
    """工具执行器。8 步 pipeline：权限 → 查找 → 校验 → 注入 → 缓存 → 执行 → 后处理 → 缓存写。"""

    def __init__(self, ctx: AgentContext, orchestrator: Orchestrator) -> None:
        self.ctx = ctx
        self.orch = orchestrator
        self.services = ctx.services
        self.semaphore = asyncio.Semaphore(ctx.spec.max_parallel_tools if ctx.spec else 3)

    async def execute(self, calls: list[ToolCall]) -> ExecutionReport:
        started = now_ts()
        tasks = [asyncio.create_task(self._execute_one(c)) for c in calls]
        results: list[ToolResult] = []
        for t in tasks:
            try:
                r = await t
                results.append(r)
            except Exception:
                pass
        return ExecutionReport(
            results=results,
            total_duration_ms=int((now_ts() - started) * 1000),
            parallel_count=len(calls),
            failed_count=sum(1 for r in results if r.status != "ok"),
        )

    async def _execute_one(self, call: ToolCall) -> ToolResult:
        async with self.semaphore:
            return await self._pipeline(call)

    async def _pipeline(self, call: ToolCall) -> ToolResult:
        started = now_ts()
        await self._publish(EventType.TOOL_STARTED, call=call)

        try:
            # [1] permission
            await self._check_permission(call)

            # [2] lookup
            tool = self._lookup(call.name)

            # [3] schema validate
            self._validate_schema(tool, call.arguments)

            # [5] idempotent cache check (skipped - no Cache on Services)
            # [6] execute with timeout + cancel
            raw_output = await self._execute_with_timeout(tool, call)

            # [7] post-process (artifactization for large outputs)
            result = await self._post_process(tool, call, raw_output)

            # [8] cache write skipped for now

            return result

        except PermissionDeniedError as e:
            return self._finalize(call, "denied", str(e),
                                  error_type="PermissionDenied", started=started)
        except SchemaValidationError as e:
            return self._finalize(call, "error", str(e),
                                  error_type="invalid_args", started=started)
        except TimeoutError as e:
            return self._finalize(call, "timeout", str(e),
                                  error_type="Timeout", started=started)
        except asyncio.CancelledError:
            return self._finalize(call, "cancelled", "cancelled",
                                  error_type="Cancelled", started=started)
        except Exception as e:
            return self._finalize(call, "error", str(e),
                                  error_type=type(e).__name__, started=started)
        finally:
            await self._publish(
                EventType.TOOL_COMPLETED,
                call=call,
                duration_ms=int((now_ts() - started) * 1000),
            )

    async def _check_permission(self, call: ToolCall) -> None:
        spec = self.ctx.spec
        # 明确禁止的工具直接拒绝
        if spec and spec.denied_tools and call.name in spec.denied_tools:
            raise PermissionDeniedError(
                f"agent '{spec.name}' is denied from calling '{call.name}'"
            )
        if spec and spec.allowed_tools and call.name not in spec.allowed_tools:
            tool = self.services.tool.get(call.name) if self.services.tool else None
            if tool is not None and tool.is_builtin:
                return
            active_custom = self.ctx.extra_inputs.get("active_custom_tools", set())
            if call.name not in active_custom:
                raise PermissionDeniedError(
                    f"agent '{spec.name}' not allowed to call '{call.name}'"
                )

    def _lookup(self, name: str) -> Tool:
        tool = self.services.tool.get(name) if self.services.tool else None
        if not tool:
            raise NotFoundError(f"tool not found: {name}")
        return tool

    def _validate_schema(self, tool: Tool, args: dict) -> None:
        try:
            jsonschema.validate(args, tool.schema)
        except jsonschema.ValidationError as e:
            raise SchemaValidationError(
                f"invalid args for {tool.name}: {e.message}"
            ) from e

    def _make_cache_key(self, tool: Tool, args: dict) -> str:
        norm = json.dumps(args, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha256(f"{tool.name}|{norm}".encode()).hexdigest()[:16]
        return f"toolcache:{self.ctx.user_id}:{tool.name}:{h}"

    async def _execute_with_timeout(self, tool: Tool, call: ToolCall) -> Any:
        timeout = getattr(tool, "timeout_sec", 60)
        deadline = self.ctx.deadline_at
        if deadline > 0:
            remaining = deadline - now_ts()
            if remaining <= 0:
                raise TimeoutError("run deadline reached")
            timeout = min(timeout, remaining)

        exec_coro = tool.execute(self.ctx, call.arguments)
        cancel_coro = self.ctx.cancel_event.wait()

        try:
            done, pending = await asyncio.wait(
                [asyncio.create_task(exec_coro),
                 asyncio.create_task(cancel_coro)],
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

            if not done:
                raise TimeoutError(f"tool {tool.name} timeout after {timeout}s")

            finished = done.pop()
            # Check if cancel triggered
            if len(done) > 0:
                raise asyncio.CancelledError()

            return finished.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise

    async def _post_process(
        self, tool: Tool, call: ToolCall, raw_output: Any
    ) -> ToolResult:
        text = self._stringify(raw_output)

        if len(text) <= LLM_INJECT_LIMIT:
            return self._finalize(call, "ok", text, started=0)

        # Large output → artifact
        artifact_svc = self.services.artifact
        if artifact_svc is None:
            return self._finalize(call, "ok", text[:LLM_INJECT_LIMIT] + "\n...[truncated]", started=0)

        try:
            art = await artifact_svc.save(
                content=text,
                user_id=self.ctx.user_id,
                source_type="tool_output",
                source_run_id=self.ctx.run_id,
                source_session_id=self.ctx.session_id,
                source_tool_name=tool.name,
                source_call_id=call.call_id,
                title=f"{tool.name} output",
            )
            content = (
                f'<artifact id="{art.artifact_id}" mime="{art.mime_type}" '
                f'size="{art.size_bytes}">\n{art.summary or text[:500]}\n</artifact>'
            )
            return self._finalize(call, "ok", content, started=0,
                                  artifact_id=art.artifact_id)
        except Exception:
            return self._finalize(call, "ok", text[:LLM_INJECT_LIMIT] + "\n...[truncated]", started=0)

    async def _publish(self, event_type: str, *, call: ToolCall, **extra) -> None:
        bus = self.services.event_bus
        if bus is None:
            return
        await bus.publish(Event(
            event_id=EVENT_ID(),
            type=event_type,
            run_id=self.ctx.run_id,
            parent_run_id=self.ctx.parent_run_id,
            session_id=self.ctx.session_id,
            user_id=self.ctx.user_id,
            ts=now_ts(),
            payload={
                "call_id": call.call_id,
                "tool_name": call.name,
                "arguments": call.arguments,
                **extra,
            },
        ))

    def _finalize(
        self,
        call: ToolCall,
        status: str,
        content: str = "",
        *,
        error_type: str | None = None,
        artifact_id: str | None = None,
        started: float = 0,
    ) -> ToolResult:
        duration_ms = int((now_ts() - started) * 1000) if started > 0 else 0
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            status=status,  # type: ignore[arg-type]
            content=content,
            artifact_id=artifact_id,
            error_type=error_type,
            error_message=content if status != "ok" else None,
            duration_ms=duration_ms,
        )

    @staticmethod
    def _stringify(output: Any) -> str:
        if isinstance(output, str):
            return output
        if isinstance(output, (dict, list)):
            return json.dumps(output, ensure_ascii=False, indent=2)
        return str(output)
