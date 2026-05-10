from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from src.gateway.deps import auth_user_id, get_runtime
from src.gateway.schemas import (
    ListMemoryOut,
    MemoryItemOut,
    RecallIn,
    RecallOut,
    RememberIn,
    memory_to_out,
)
from src.runtime.runtime import Runtime

router = APIRouter()


@router.post("/items", response_model=MemoryItemOut, status_code=201)
async def remember_explicit(
    body: RememberIn,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> MemoryItemOut:
    svc = runtime.services.memory
    if svc is None:
        raise HTTPException(500, "memory service not available")
    record = await svc.remember(
        user_id=user_id,
        kind=body.kind,
        content=body.content,
        importance=body.importance,
        confidence=body.confidence,
        tags=body.tags,
        occurred_at=body.occurred_at,
    )
    return MemoryItemOut(**memory_to_out(record))


@router.delete("/items/{memory_id}", status_code=204)
async def forget(
    memory_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> None:
    svc = runtime.services.memory
    if svc is None:
        raise HTTPException(500, "memory service not available")
    await svc.forget(memory_id, user_id=user_id)


@router.post("/recall", response_model=RecallOut)
async def recall(
    body: RecallIn,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> RecallOut:
    svc = runtime.services.memory
    if svc is None:
        raise HTTPException(500, "memory service not available")
    items = await svc.recall(
        user_id=user_id,
        query=body.query,
        top_k=body.top_k,
        kinds=body.kinds,
    )
    return RecallOut(items=[MemoryItemOut(**memory_to_out(m)) for m in items])


@router.get("/items", response_model=ListMemoryOut)
async def list_memories(
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    kind: str | None = Query(default=None),
    state: str = Query(default="active"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> ListMemoryOut:
    svc = runtime.services.memory
    if svc is None:
        raise HTTPException(500, "memory service not available")
    items = await svc.list_(
        user_id=user_id,
        kind=kind,
        state=state,
        limit=limit,
        cursor=cursor,
    )
    next_cursor = items[-1].memory_id if len(items) >= limit else None
    return ListMemoryOut(
        items=[MemoryItemOut(**memory_to_out(m)) for m in items],
        next_cursor=next_cursor,
    )
