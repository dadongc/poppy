from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import Header, HTTPException, Query, Request

if TYPE_CHECKING:
    from src.runtime.runtime import Runtime


def _verify_token(request: Request, token: str) -> str:
    runtime: Runtime = request.app.state.runtime
    expected = runtime._config.gateway.api_key
    if not expected:
        return "default"
    if token != expected:
        raise HTTPException(401, "invalid token")
    return "default"


async def auth_user_id(
    request: Request,
    authorization: str = Header(...),
) -> str:
    """从 Bearer Token 解析出 user_id。个人版直接 hardcode 单用户。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization[len("Bearer "):]
    return _verify_token(request, token)


async def auth_user_id_sse(
    request: Request,
    authorization: str | None = Header(None),
    token: str | None = Query(None),
) -> str:
    """SSE 认证。EventSource 不支持自定义 header，允许通过 query param 传 token。"""
    if authorization and authorization.startswith("Bearer "):
        return _verify_token(request, authorization[len("Bearer "):])
    if token:
        return _verify_token(request, token)
    # 无认证信息时，尝试以空 token 校验（dev 模式下 api_key 为空会放行）
    return _verify_token(request, "")


async def get_runtime(request: Request) -> Runtime:
    from src.runtime.runtime import Runtime

    return Runtime.current()


async def get_trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", str(uuid.uuid4()))
