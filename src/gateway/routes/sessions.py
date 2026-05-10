from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query

from src.gateway.deps import auth_user_id, get_runtime
from src.gateway.schemas import (
    CreateSessionIn,
    CreateSessionOut,
    ListMessagesOut,
    ListSessionsOut,
    MessageItem,
    SessionItem,
)
from src.runtime.runtime import Runtime

router = APIRouter()


@router.post("", response_model=CreateSessionOut, status_code=201)
async def create_session(
    body: CreateSessionIn,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> CreateSessionOut:
    svc = runtime.services.session
    if svc is None:
        raise RuntimeError("session service not available")
    info = await svc.create(user_id=user_id, title=body.title)
    return CreateSessionOut(session_id=info.session_id, created_at=info.created_at)


@router.get("", response_model=ListSessionsOut)
async def list_sessions(
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> ListSessionsOut:
    svc = runtime.services.session
    if svc is None:
        raise RuntimeError("session service not available")
    items = await svc.list_by_user(user_id, limit=limit, cursor=cursor)
    next_cursor = str(items[-1].last_active_at) if len(items) >= limit else None
    return ListSessionsOut(
        items=[
            SessionItem(
                session_id=s.session_id,
                title=s.title,
                created_at=s.created_at,
                last_active_at=s.last_active_at,
                message_count=s.message_count,
            )
            for s in items
        ],
        next_cursor=next_cursor,
    )


@router.get("/{session_id}/messages", response_model=ListMessagesOut)
async def list_messages(
    session_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    limit: int = Query(default=50, ge=1, le=200),
    before_seq: int | None = Query(default=None),
) -> ListMessagesOut:
    svc = runtime.services.session
    if svc is None:
        raise RuntimeError("session service not available")
    msgs = await svc.get_recent(
        session_id=session_id,
        user_id=user_id,
        limit=limit,
        before_seq=before_seq,
    )
    return ListMessagesOut(
        messages=[
            MessageItem(
                msg_id=m.msg_id,
                session_id=m.session_id,
                seq=m.seq,
                run_id=m.run_id,
                role=m.role,
                content=m.content,
                tool_calls=[asdict(tc) for tc in m.tool_calls],
                tool_call_id=m.tool_call_id,
                name=m.name,
                artifact_refs=m.artifact_refs,
                created_at=m.created_at,
            )
            for m in msgs
        ],
    )
