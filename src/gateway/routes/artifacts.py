from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse, Response

from src.gateway.deps import auth_user_id, get_runtime
from src.gateway.schemas import ArtifactOut, artifact_to_out
from src.runtime.runtime import Runtime

router = APIRouter()


@router.post("", response_model=ArtifactOut, status_code=201)
async def upload_artifact(
    file: UploadFile = File(...),
    title: str = Form(""),
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> ArtifactOut:
    svc = runtime.services.artifact
    if svc is None:
        raise HTTPException(500, "artifact service not available")

    async def stream_bytes():
        while chunk := await file.read(64 * 1024):
            yield chunk

    artifact = await svc.save_stream(
        user_id=user_id,
        stream=stream_bytes(),
        mime_type=file.content_type or "application/octet-stream",
        title=title or file.filename or "untitled",
    )
    return ArtifactOut(**artifact_to_out(artifact))


@router.get("/{artifact_id}", response_class=Response)
async def download_artifact(
    artifact_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    inline: bool = Query(default=False),
) -> Response:
    svc = runtime.services.artifact
    if svc is None:
        raise HTTPException(500, "artifact service not available")

    try:
        meta = await svc.get_metadata(artifact_id, user_id=user_id)
    except Exception as err:
        raise HTTPException(404, "artifact not found") from err

    if meta is None:
        raise HTTPException(404, "artifact not found")

    # 大文件 302 到签名 URL
    if meta.size_bytes > 10 * 1024 * 1024:
        signed = await svc.get_signed_url(artifact_id, user_id=user_id)
        return RedirectResponse(signed)

    content = await svc.get_content(artifact_id, user_id=user_id)
    disposition = "inline" if inline else f'attachment; filename="{meta.title}"'
    return Response(
        content=content,
        media_type=meta.mime_type,
        headers={"Content-Disposition": disposition},
    )


@router.get("/{artifact_id}/meta", response_model=ArtifactOut)
async def get_artifact_meta(
    artifact_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> ArtifactOut:
    svc = runtime.services.artifact
    if svc is None:
        raise HTTPException(500, "artifact service not available")
    try:
        meta = await svc.get_metadata(artifact_id, user_id=user_id)
    except Exception as err:
        raise HTTPException(404, "artifact not found") from err
    if meta is None:
        raise HTTPException(404, "artifact not found")
    return ArtifactOut(**artifact_to_out(meta))


@router.delete("/{artifact_id}", status_code=204)
async def delete_artifact(
    artifact_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> None:
    svc = runtime.services.artifact
    if svc is None:
        raise HTTPException(500, "artifact service not available")
    try:
        await svc.delete(artifact_id, user_id=user_id)
    except Exception as err:
        raise HTTPException(404, "artifact not found") from err
