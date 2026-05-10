from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from src.gateway.errors import register_exception_handlers
from src.gateway.middleware.access_log import AccessLogMiddleware
from src.gateway.middleware.trace_id import TraceIdMiddleware
from src.gateway.routes import agents, artifacts, chat, kb, memory, runs, sessions

logger = logging.getLogger("gateway")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from src.runtime.runtime import Runtime

    config_path = os.environ.get("CONFIG_PATH", "config/dev.yaml")
    logger.info("Initializing runtime from %s", config_path)
    runtime = await Runtime.initialize(config_path)
    app.state.runtime = runtime
    logger.info("Runtime initialized, gateway ready")
    try:
        yield
    finally:
        logger.info("Shutting down runtime")
        await runtime.shutdown(timeout=30)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Poppy Personal Assistant",
        lifespan=lifespan,
        default_response_class=JSONResponse,
    )

    # 中间件 (LIFO order — last added runs first)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(TraceIdMiddleware)

    # 异常映射
    register_exception_handlers(app)

    # 路由
    app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
    app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
    app.include_router(artifacts.router, prefix="/api/artifacts", tags=["artifacts"])
    app.include_router(memory.router, prefix="/api/memory", tags=["memory"])
    app.include_router(kb.router, prefix="/api/kb", tags=["kb"])
    app.include_router(agents.router, prefix="/api/agents", tags=["agents"])
    app.include_router(chat.router, tags=["chat"])

    return app


app = create_app()
