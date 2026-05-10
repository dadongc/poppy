from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from src.common.types import Event, EventType

if TYPE_CHECKING:
    from fastapi import Request

    from src.runtime.runtime import Runtime

PUBLIC_EVENT_TYPES: set[str] = {
    EventType.RUN_STARTED,
    EventType.RUN_COMPLETED,
    EventType.RUN_FAILED,
    EventType.RUN_CANCELLED,
    EventType.RUN_TIMEOUT,
    EventType.STEP_STARTED,
    EventType.STEP_COMPLETED,
    EventType.LLM_TEXT_DELTA,
    EventType.LLM_TOOL_CALL_START,
    EventType.LLM_TOOL_CALL_END,
    EventType.LLM_USAGE,
    EventType.TOOL_STARTED,
    EventType.TOOL_COMPLETED,
    EventType.TOOL_FAILED,
    EventType.SUBAGENT_STARTED,
    EventType.SUBAGENT_COMPLETED,
    EventType.ARTIFACT_CREATED,
}

TERMINAL_STATES: set[str] = {"completed", "failed", "cancelled", "timeout"}

TERMINAL_EVENT_TYPES: set[str] = {
    EventType.RUN_COMPLETED,
    EventType.RUN_FAILED,
    EventType.RUN_CANCELLED,
    EventType.RUN_TIMEOUT,
}


async def sse_event_stream(
    runtime: Runtime,
    run_id: str,
    user_id: str,
    since_seq: int,
    request: Request,
) -> AsyncIterator[str]:
    """生成 SSE 帧。先回放历史,再切实时订阅。"""
    bus = runtime.services.event_bus
    reg = runtime.services.run_registry
    if bus is None or reg is None:
        yield _format_done()
        return

    # 1. 回放历史
    async for ev in bus.replay(run_id, since_seq=since_seq):
        if ev.type in PUBLIC_EVENT_TYPES and ev.scope == "public":
            yield _format_sse(ev)

    # 2. 若已终态,直接 done
    info = await reg.get(run_id)
    if info and info.state in TERMINAL_STATES:
        yield _format_done()
        return

    # 3. 实时订阅
    sub = bus.subscribe({"run_id": run_id, "scope": "public"})
    try:
        async with sub:
            async for ev in _heartbeat(sub, interval=15):
                if await request.is_disconnected():
                    break

                if ev.type not in PUBLIC_EVENT_TYPES:
                    continue

                yield _format_sse(ev)

                if ev.type in TERMINAL_EVENT_TYPES:
                    yield _format_done()
                    break
    except Exception:
        yield _format_done()


async def _heartbeat(
    stream: AsyncIterator[Event],
    interval: float = 15,
) -> AsyncIterator[Event]:
    """在流上叠加心跳,防止反向代理超时关连接。"""
    q: asyncio.Queue[Event | None] = asyncio.Queue()

    async def producer() -> None:
        async for item in stream:
            await q.put(item)
        await q.put(None)

    task = asyncio.create_task(producer())
    try:
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=interval)
                if item is None:
                    return
                yield item
            except TimeoutError:
                continue
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _format_sse(ev: Event) -> str:
    """SSE 帧格式：id/event/data。"""
    return (
        f"id: {ev.seq}\n"
        f"event: {ev.type}\n"
        f"data: {json.dumps({'payload': ev.payload, 'ts': ev.ts}, ensure_ascii=False)}\n\n"
    )


def _format_done() -> str:
    return "event: done\ndata: {}\n\n"
