from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.common.errors import (
    AgentError,
    BudgetExceededError,
    CancelledError,
    ConfigError,
    ConflictError,
    InfraError,
    LLMProviderError,
    NotFoundError,
    PermissionDeniedError,
    TimeoutError,
    ToolError,
)

ERROR_MAP: dict[type[AgentError], tuple[int, str]] = {
    NotFoundError: (404, "not_found"),
    PermissionDeniedError: (403, "permission_denied"),
    ConflictError: (409, "conflict"),
    BudgetExceededError: (429, "budget_exceeded"),
    CancelledError: (499, "cancelled"),
    TimeoutError: (504, "timeout"),
    LLMProviderError: (502, "llm_provider_error"),
    ToolError: (502, "tool_error"),
    InfraError: (503, "infra_error"),
    ConfigError: (500, "config_error"),
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AgentError)
    async def handle_agent_error(request: Request, exc: AgentError) -> JSONResponse:
        for cls, (code, error_type) in ERROR_MAP.items():
            if isinstance(exc, cls):
                return JSONResponse(
                    status_code=code,
                    content={
                        "error": error_type,
                        "message": str(exc),
                        "trace_id": getattr(request.state, "trace_id", ""),
                    },
                )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal",
                "message": str(exc),
                "trace_id": getattr(request.state, "trace_id", ""),
            },
        )
