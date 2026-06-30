from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

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


def _effective_user(auth_user: str, override: str | None) -> str:
    """允许 query param 覆盖 auth user_id（调试/管理用）。"""
    return override or auth_user


@router.post("/items", response_model=MemoryItemOut, status_code=201)
async def remember_explicit(
    body: RememberIn,
    request: Request,
    auth_user: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    user_id: str | None = Query(default=None),
) -> MemoryItemOut:
    svc = runtime.services.memory
    if svc is None:
        raise HTTPException(500, "memory service not available")
    effective = _effective_user(auth_user, user_id)
    record = await svc.remember(
        user_id=effective,
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
    request: Request,
    auth_user: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    user_id: str | None = Query(default=None),
) -> None:
    svc = runtime.services.memory
    if svc is None:
        raise HTTPException(500, "memory service not available")
    await svc.forget(memory_id, user_id=_effective_user(auth_user, user_id))


@router.post("/recall", response_model=RecallOut)
async def recall(
    body: RecallIn,
    request: Request,
    auth_user: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    user_id: str | None = Query(default=None),
) -> RecallOut:
    svc = runtime.services.memory
    if svc is None:
        raise HTTPException(500, "memory service not available")
    effective = _effective_user(auth_user, user_id)
    items = await svc.recall(
        user_id=effective,
        query=body.query,
        top_k=body.top_k,
        kinds=body.kinds,
    )
    return RecallOut(items=[MemoryItemOut(**memory_to_out(m)) for m in items])


@router.get("/items", response_model=ListMemoryOut)
async def list_memories(
    request: Request,
    auth_user: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    user_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    state: str = Query(default="active"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> ListMemoryOut:
    svc = runtime.services.memory
    if svc is None:
        raise HTTPException(500, "memory service not available")
    effective = _effective_user(auth_user, user_id)
    items = await svc.list_(
        user_id=effective,
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
