# 40 · Gateway 层（FastAPI HTTP/SSE 入口）

> 唯一的对外协议层。负责鉴权、协议转换（HTTP → Runtime API、SSE 流式输出）、文件上传、错误码映射。

实现位置：`src/gateway/`

---

## 1. 目录结构

```
src/gateway/
├── __init__.py
├── app.py                  # FastAPI 实例 + lifespan + 全局中间件
├── deps.py                 # 依赖注入（auth、runtime 句柄）
├── errors.py               # 异常 → HTTP 错误响应映射
├── sse.py                  # SSE 适配器
├── routes/
│   ├── __init__.py
│   ├── sessions.py         # /api/sessions
│   ├── runs.py             # /api/runs
│   ├── artifacts.py        # /api/artifacts
│   ├── memory.py           # /api/memory
│   ├── kb.py               # /api/kb
│   └── agents.py           # /api/agents
└── schemas.py              # 全部 Pydantic 入参/出参
```

---

## 2. 应用装配

```python
# src/gateway/app.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from src.runtime.runtime import Runtime
from src.common.config import load_config
from .errors import register_exception_handlers
from .routes import sessions, runs, artifacts, memory, kb, agents

@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config(os.environ.get("CONFIG_PATH", "config/dev.yaml"))
    runtime = await Runtime.initialize(cfg)
    app.state.runtime = runtime
    try:
        yield
    finally:
        await runtime.shutdown(timeout=30)

def create_app() -> FastAPI:
    app = FastAPI(
        title="Personal Assistant Gateway",
        lifespan=lifespan,
        default_response_class=JSONResponse,
    )

    # 中间件
    app.add_middleware(CORSMiddleware, allow_origins=cfg_origins(), ...)
    app.add_middleware(TraceIdMiddleware)
    app.add_middleware(AccessLogMiddleware)

    # 异常映射
    register_exception_handlers(app)

    # 路由
    app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
    app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
    app.include_router(artifacts.router, prefix="/api/artifacts", tags=["artifacts"])
    app.include_router(memory.router, prefix="/api/memory", tags=["memory"])
    app.include_router(kb.router, prefix="/api/kb", tags=["kb"])
    app.include_router(agents.router, prefix="/api/agents", tags=["agents"])

    return app

app = create_app()
```

---

## 3. 鉴权（个人版 API Key）

```python
# src/gateway/deps.py
from fastapi import Header, HTTPException, Request

async def auth_user_id(
    request: Request,
    authorization: str = Header(...),
) -> str:
    """从 Bearer Token 解析出 user_id。个人版直接 hardcode 单用户。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization[len("Bearer "):]
    runtime: Runtime = request.app.state.runtime
    user_id = runtime.config.gateway.token_to_user_id(token)
    if not user_id:
        raise HTTPException(401, "invalid token")
    return user_id

async def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime

async def get_trace_id(request: Request) -> str:
    return request.state.trace_id
```

后续多用户：把 token 改成 JWT,在中间件里验签 + 缓存。

---

## 4. 路由设计

### 4.1 Sessions

```python
# src/gateway/routes/sessions.py
@router.post("", response_model=CreateSessionOut)
async def create_session(
    body: CreateSessionIn,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
):
    info = await runtime.services.session.create(
        user_id=user_id, title=body.title or "",
    )
    return CreateSessionOut(session_id=info.session_id, created_at=info.created_at)

@router.get("", response_model=ListSessionsOut)
async def list_sessions(
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    limit: int = 20,
    cursor: str | None = None,
):
    items, next_cursor = await runtime.services.session.list_(user_id, limit, cursor)
    return ListSessionsOut(items=items, next_cursor=next_cursor)

@router.get("/{session_id}/messages", response_model=ListMessagesOut)
async def list_messages(
    session_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    limit: int = 50,
    before_seq: int | None = None,
):
    msgs = await runtime.services.session.list_messages(
        session_id=session_id, user_id=user_id, limit=limit, before_seq=before_seq,
    )
    return ListMessagesOut(messages=[asdict(m) for m in msgs])
```

### 4.2 Runs（核心）

```python
# src/gateway/routes/runs.py
@router.post("", response_model=StartRunOut, status_code=202)
async def start_run(
    body: StartRunIn,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    trace_id: str = Depends(get_trace_id),
):
    """启动一次 run。立即返回 run_id,实际执行异步。"""
    run_id = await runtime.start_run(
        user_id=user_id,
        session_id=body.session_id,
        agent_name=body.agent_name or "default",
        user_message=body.message,
        artifact_refs=body.artifact_refs or [],
        trace_id=trace_id,
    )
    return StartRunOut(run_id=run_id)

@router.get("/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
):
    """SSE 事件流。支持断点续传（Last-Event-ID = 最后收到的 seq）。"""
    since_seq = int(last_event_id) if last_event_id else 0

    # 权限校验：run 是否属于当前 user
    info = await runtime.run_registry.get(run_id)
    if not info or info.user_id != user_id:
        raise HTTPException(404, "run not found")

    return StreamingResponse(
        sse_event_stream(runtime, run_id, user_id, since_seq, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
):
    info = await runtime.run_registry.get(run_id)
    if not info or info.user_id != user_id:
        raise HTTPException(404, "run not found")
    n = await runtime.run_registry.cancel(run_id)
    return {"cancelled_count": n}

@router.get("/{run_id}", response_model=RunInfoOut)
async def get_run(
    run_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
):
    info = await runtime.run_registry.get(run_id)
    if not info or info.user_id != user_id:
        raise HTTPException(404)
    return RunInfoOut(**asdict(info))
```

### 4.3 Artifacts

```python
# src/gateway/routes/artifacts.py
@router.post("", response_model=ArtifactOut, status_code=201)
async def upload_artifact(
    file: UploadFile = File(...),
    title: str = Form(""),
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
):
    # 流式读取,避免大文件占内存
    async def stream_bytes():
        while chunk := await file.read(64 * 1024):
            yield chunk

    artifact = await runtime.services.artifact.save_stream(
        stream=stream_bytes(),
        user_id=user_id,
        mime_type=file.content_type,
        title=title or file.filename,
        source_type="user_upload",
    )
    return ArtifactOut(**_artifact_to_dict(artifact))

@router.get("/{artifact_id}", response_class=Response)
async def download_artifact(
    artifact_id: str,
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
    inline: bool = False,
):
    artifact = await runtime.services.artifact.get(artifact_id, user_id=user_id)
    if not artifact:
        raise HTTPException(404)
    # 大文件直接 302 到 OSS 签名 URL;小文件直接代理流
    if artifact.size_bytes > 10 * 1024 * 1024:
        signed = await runtime.services.artifact.signed_url(artifact_id, ttl=300)
        return RedirectResponse(signed)
    content = await runtime.services.artifact.read(artifact_id)
    disposition = "inline" if inline else f'attachment; filename="{artifact.title}"'
    return Response(
        content=content,
        media_type=artifact.mime_type,
        headers={"Content-Disposition": disposition},
    )

@router.get("/{artifact_id}/meta", response_model=ArtifactOut)
async def get_artifact_meta(...):
    ...
```

### 4.4 Memory / KB / Agents

```python
# memory
@router.post("/items")
async def remember_explicit(...):
    """显式 remember API。"""

@router.delete("/items/{memory_id}")
async def forget(...):
    ...

# kb
@router.post("/documents")
async def ingest_document(body: IngestDocIn, ...):
    """注册一个 KB 文档（异步 ingest）。"""

@router.get("/documents")
async def list_documents(...):
    ...

# agents
@router.get("")
async def list_agents(runtime: Runtime = Depends(get_runtime)):
    return [asdict(s) for s in runtime.agent_registry.list_()]
```

---

## 5. SSE Adapter

```python
# src/gateway/sse.py
import json
from src.runtime.event_bus import EventFilter
from src.common.types import EventType

# 推送给前端的事件类型白名单（scope=public）
PUBLIC_EVENT_TYPES = {
    EventType.RUN_STARTED, EventType.RUN_COMPLETED,
    EventType.RUN_FAILED, EventType.RUN_CANCELLED, EventType.RUN_TIMEOUT,
    EventType.STEP_STARTED, EventType.STEP_COMPLETED,
    EventType.LLM_TEXT_DELTA,
    EventType.LLM_TOOL_CALL_START, EventType.LLM_TOOL_CALL_END,
    EventType.LLM_USAGE,
    EventType.TOOL_STARTED, EventType.TOOL_COMPLETED, EventType.TOOL_FAILED,
    EventType.SUBAGENT_STARTED, EventType.SUBAGENT_COMPLETED,
    EventType.ARTIFACT_CREATED,
}

async def sse_event_stream(runtime, run_id, user_id, since_seq, request):
    """生成 SSE 帧。先回放历史,再切实时订阅。"""
    info = await runtime.run_registry.get(run_id)
    last_seq = since_seq

    # 1. 回放历史
    async for ev in runtime.event_bus.replay(run_id, since_seq=since_seq):
        if ev.type not in PUBLIC_EVENT_TYPES or ev.scope != "public":
            continue
        last_seq = ev.seq
        yield _format(ev)

    # 2. 若已终态,直接 done
    info = await runtime.run_registry.get(run_id)
    if info and info.state in TERMINAL_STATES:
        yield "event: done\ndata: {}\n\n"
        return

    # 3. 实时订阅
    sub = runtime.event_bus.subscribe(EventFilter(
        run_id=run_id, types=PUBLIC_EVENT_TYPES, scope="public", since_seq=last_seq,
    ))
    try:
        async for ev in sub:
            # 客户端断开
            if await request.is_disconnected():
                break
            yield _format(ev)
            if ev.type in TERMINAL_EVENT_TYPES:
                yield "event: done\ndata: {}\n\n"
                break
    finally:
        await sub.aclose()


def _format(ev) -> str:
    """SSE 帧格式：event/id/data。"""
    return (
        f"id: {ev.seq}\n"
        f"event: {ev.type}\n"
        f"data: {json.dumps({'payload': ev.payload, 'ts': ev.ts}, ensure_ascii=False)}\n\n"
    )

TERMINAL_STATES = {"completed", "failed", "cancelled", "timeout"}
TERMINAL_EVENT_TYPES = {
    EventType.RUN_COMPLETED, EventType.RUN_FAILED,
    EventType.RUN_CANCELLED, EventType.RUN_TIMEOUT,
}
```

### 5.1 SSE 心跳

为防止反向代理（nginx/CDN）超时关连接,每 15s 发送一条 comment 帧：

```python
async def with_heartbeat(stream, interval=15):
    queue: asyncio.Queue = asyncio.Queue()

    async def producer():
        async for chunk in stream:
            await queue.put(chunk)
        await queue.put(None)

    asyncio.create_task(producer())
    while True:
        try:
            chunk = await asyncio.wait_for(queue.get(), timeout=interval)
            if chunk is None:
                return
            yield chunk
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"   # SSE 注释帧
```

---

## 6. Schemas（Pydantic）

```python
# src/gateway/schemas.py
from pydantic import BaseModel, Field

class CreateSessionIn(BaseModel):
    title: str | None = None

class CreateSessionOut(BaseModel):
    session_id: str
    created_at: float

class StartRunIn(BaseModel):
    session_id: str
    message: str
    agent_name: str | None = "default"
    artifact_refs: list[str] | None = None

class StartRunOut(BaseModel):
    run_id: str

class RunInfoOut(BaseModel):
    run_id: str
    parent_run_id: str | None
    session_id: str
    user_id: str
    agent_name: str
    state: str
    started_at: float
    finished_at: float | None
    error: str
    used_tokens: int
    used_steps: int

class ArtifactOut(BaseModel):
    artifact_id: str
    title: str
    mime_type: str
    size_bytes: int
    summary: str
    created_at: float
```

---

## 7. 错误处理

```python
# src/gateway/errors.py
from fastapi import Request
from fastapi.responses import JSONResponse
from src.common.errors import (
    NotFoundError, PermissionError, ConfigError,
    BudgetExceededError, CancelledError, TimeoutError,
    LLMProviderError, ToolError, AgentError, InfraError,
)

ERROR_MAP = {
    NotFoundError: (404, "not_found"),
    PermissionError: (403, "permission_denied"),
    ConfigError: (500, "config_error"),
    BudgetExceededError: (429, "budget_exceeded"),
    CancelledError: (499, "cancelled"),
    TimeoutError: (504, "timeout"),
    LLMProviderError: (502, "llm_provider_error"),
    ToolError: (502, "tool_error"),
    InfraError: (503, "infra_error"),
    AgentError: (500, "agent_error"),
}

def register_exception_handlers(app):
    @app.exception_handler(AgentError)
    async def handle(request: Request, exc: AgentError):
        for cls, (code, type_) in ERROR_MAP.items():
            if isinstance(exc, cls):
                return JSONResponse(
                    status_code=code,
                    content={
                        "error": type_,
                        "message": str(exc),
                        "trace_id": getattr(request.state, "trace_id", ""),
                    },
                )
        return JSONResponse(
            status_code=500,
            content={"error": "internal", "message": str(exc)},
        )
```

---

## 8. 中间件

### 8.1 TraceID

```python
class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        trace_id = request.headers.get("X-Trace-ID") or new_id("trace")
        request.state.trace_id = trace_id
        # structlog contextvar 绑定
        with structlog.contextvars.bound_contextvars(trace_id=trace_id):
            response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response
```

### 8.2 AccessLog

```python
class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.info(
            "http",
            method=request.method, path=request.url.path,
            status=response.status_code, duration_ms=duration_ms,
            trace_id=request.state.trace_id,
        )
        return response
```

### 8.3 RateLimit（可选）

简单 token bucket（基于 Redis）：

```python
class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # 取 user_id 从 token（与 auth 一致）
        user_id = self._extract_user(request)
        if user_id:
            allowed = await self.limiter.check(user_id, capacity=60, refill_rate=1)
            if not allowed:
                return JSONResponse(429, {"error": "rate_limited"})
        return await call_next(request)
```

---

## 9. 上传文件策略

- 默认走 `multipart/form-data`,FastAPI `UploadFile` 是流式的（不会全部加载内存）
- 大文件（> 100MB）走 OSS 直传：
  1. `POST /api/artifacts/presign` → 返回签名 URL
  2. 客户端直传 OSS
  3. `POST /api/artifacts/finalize` → 服务端登记元数据 + 触发 ingest

---

## 10. 单元测试 + 集成测试

```
tests/unit/gateway/
├── test_auth.py             # token → user_id
├── test_error_mapping.py    # 各种 AgentError → HTTP code
├── test_trace_id.py
├── test_sse_format.py       # SSE 帧格式化
├── test_sse_replay.py       # since_seq 回放 → 切实时
└── test_schemas.py          # Pydantic 校验

tests/integration/gateway/
├── test_create_session_run.py     # 全链路：建 session → start_run → 看 SSE
├── test_run_cancel.py             # cancel API → SSE 收到 cancelled
├── test_artifact_upload_dl.py     # 上传 → 下载
├── test_kb_ingest_e2e.py          # 注册 doc → 等 ready
└── test_concurrent_runs.py        # 多 run 并发,SSE 互不干扰
```

### 关键测试

```python
@pytest.mark.asyncio
async def test_sse_reconnect_resume():
    """断线重连,Last-Event-ID 续传。"""
    async with httpx.AsyncClient(app=app, base_url="http://test") as c:
        # start run
        r = await c.post("/api/runs", json={"session_id": s, "message": "hi"})
        run_id = r.json()["run_id"]
        # 接收前 3 个事件就断开
        async with c.stream("GET", f"/api/runs/{run_id}/events") as resp:
            events_first = await collect_n_events(resp, n=3)
        last_seq = events_first[-1]["seq"]

        # 重连
        async with c.stream(
            "GET", f"/api/runs/{run_id}/events",
            headers={"Last-Event-ID": str(last_seq)},
        ) as resp:
            events_second = await collect_until_done(resp)
        # 不应包含 last_seq 之前的事件
        assert all(e["seq"] > last_seq for e in events_second)
```

---

## 11. 部署 & 性能

- 启动：`uvicorn src.gateway.app:app --host 0.0.0.0 --port 8000 --workers 1 --loop uvloop --http httptools`
- **`--workers 1`** 是关键：多 worker 会破坏 InProcessEventBus 的语义（事件无法跨进程）。要扩展用 Redis Pub/Sub 桥接
- SSE 连接保活：客户端无活动 60s 发心跳,nginx `proxy_read_timeout 1h`
- gzip 不要对 SSE 路径开启（会缓冲断帧）

---

## 12. 与其他模块的契约

| 路由 | 调用 | 说明 |
|---|---|---|
| POST /runs | `runtime.start_run()` | 异步启动,立即返回 run_id |
| GET /runs/{id}/events | `event_bus.replay() + subscribe()` | SSE,支持续传 |
| POST /runs/{id}/cancel | `run_registry.cancel()` | 级联取消所有后代 |
| POST /artifacts | `artifact_store.save_stream()` | 流式写入 OSS |
| GET /artifacts/{id} | `artifact_store.signed_url() / read()` | 大文件 302 |
| POST /memory/items | `memory_service.remember()` | 显式记忆 |
| POST /kb/documents | `kb_service.ingest()` | 异步 ingest |

---

## 13. FAQ

**Q: 为什么不直接 WebSocket 双向?**
A: SSE 单向 server→client 已满足,且 SSE 自带断线重连 + 简单文本协议,调试方便。WebSocket 留给未来"边输入边修改 prompt"等场景。

**Q: 为什么 SSE 用 `id: <seq>` 而不是 event_id?**
A: 浏览器原生 EventSource 的 `Last-Event-ID` 自动回传机制需要 id 是有意义的标识。用 seq（同 run 内单调）足够,event_id 是 ULID 全局唯一但跨 run 无法直接做 since 比较。

**Q: 为什么 cancel 是 POST 而不是 DELETE?**
A: cancel 是动作（动作型 API）,DELETE 语义偏向删除资源。RESTful 风格优先表达意图。