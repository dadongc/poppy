from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.gateway.deps import auth_user_id, auth_user_id_sse, get_runtime, get_trace_id
from src.gateway.schemas import RunInfoOut, StartRunIn, StartRunOut, run_info_to_out
from src.gateway.sse import sse_event_stream
from src.runtime.runtime import Runtime

router = APIRouter()


@router.post("", response_model=StartRunOut, status_code=202)
async def start_run(
    body: StartRunIn,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    trace_id: str = Depends(get_trace_id),
) -> StartRunOut:
    run_id = await runtime.start_run(
        agent_name=body.agent_name or "default",
        user_id=user_id,
        session_id=body.session_id,
        user_message=body.message,
    )
    return StartRunOut(run_id=run_id)


@router.get("/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    user_id: str = Depends(auth_user_id_sse),
    runtime: Runtime = Depends(get_runtime),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE 事件流。支持断点续传（Last-Event-ID = 最后收到的 seq）。"""
    since_seq = int(last_event_id) if last_event_id else 0

    reg = runtime.services.run_registry
    if reg is None:
        raise HTTPException(500, "run registry not available")
    info = await reg.get(run_id)
    if not info or info.user_id != user_id:
        raise HTTPException(404, "run not found")

    return StreamingResponse(
        sse_event_stream(runtime, run_id, user_id, since_seq, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> dict:
    reg = runtime.services.run_registry
    if reg is None:
        raise HTTPException(500, "run registry not available")
    info = await reg.get(run_id)
    if not info or info.user_id != user_id:
        raise HTTPException(404, "run not found")
    n = await reg.cancel(run_id)
    return {"cancelled_count": n}


@router.get("/{run_id}", response_model=RunInfoOut)
async def get_run(
    run_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> RunInfoOut:
    reg = runtime.services.run_registry
    if reg is None:
        raise HTTPException(500, "run registry not available")
    info = await reg.get(run_id)
    if not info or info.user_id != user_id:
        raise HTTPException(404, "run not found")
    return RunInfoOut(**run_info_to_out(info))


@router.get("", response_model=list[RunInfoOut])
async def list_active_runs(
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> list[RunInfoOut]:
    reg = runtime.services.run_registry
    if reg is None:
        raise HTTPException(500, "run registry not available")
    items = await reg.list_active()
    return [
        RunInfoOut(**run_info_to_out(r))
        for r in items
        if r.user_id == user_id
    ]
