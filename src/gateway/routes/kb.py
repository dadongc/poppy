from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from src.gateway.deps import auth_user_id, get_runtime
from src.gateway.schemas import IngestDocIn, IngestDocOut, KBDocItem, ListKBDocsOut
from src.runtime.runtime import Runtime

router = APIRouter()


@router.post("/documents", response_model=IngestDocOut, status_code=202)
async def ingest_document(
    body: IngestDocIn,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> IngestDocOut:
    svc = runtime.services.kb
    if svc is None:
        raise HTTPException(500, "kb service not available")
    doc = await svc.add_document(
        user_id=user_id,
        artifact_id=body.artifact_id,
        title=body.title,
        source_type=body.source_type,
        source_uri=body.source_uri,
        tags=body.tags,
    )
    return IngestDocOut(doc_id=doc.doc_id, state=doc.state)


@router.get("/documents", response_model=ListKBDocsOut)
async def list_documents(
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> ListKBDocsOut:
    svc = runtime.services.kb
    if svc is None:
        raise HTTPException(500, "kb service not available")
    items = await svc.list_documents(
        user_id=user_id,
        state=state,
        limit=limit,
        cursor=cursor,
    )
    next_cursor = items[-1].doc_id if len(items) >= limit else None
    return ListKBDocsOut(
        items=[
            KBDocItem(
                doc_id=d.doc_id,
                title=d.title,
                source_type=d.source_type,
                source_uri=d.source_uri,
                state=d.state,
                chunk_count=d.chunk_count,
                created_at=d.created_at,
                updated_at=d.updated_at,
                error=d.error,
                tags=d.tags,
            )
            for d in items
        ],
        next_cursor=next_cursor,
    )


@router.get("/documents/{doc_id}", response_model=KBDocItem)
async def get_document(
    doc_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> KBDocItem:
    svc = runtime.services.kb
    if svc is None:
        raise HTTPException(500, "kb service not available")
    doc = await svc.get_document(doc_id, user_id=user_id)
    return KBDocItem(
        doc_id=doc.doc_id,
        title=doc.title,
        source_type=doc.source_type,
        source_uri=doc.source_uri,
        state=doc.state,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        error=doc.error,
        tags=doc.tags,
    )


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> None:
    svc = runtime.services.kb
    if svc is None:
        raise HTTPException(500, "kb service not available")
    await svc.delete_document(doc_id, user_id=user_id)
