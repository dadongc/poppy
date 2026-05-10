from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.common.errors import (
    AgentError,
    BudgetExceededError,
    CancelledError,
    ConflictError,
    InfraError,
    LLMProviderError,
    NotFoundError,
    PermissionDeniedError,
    TimeoutError,
    ToolError,
)
from src.gateway.errors import ERROR_MAP, register_exception_handlers


@pytest.fixture
def app():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise/{error_type}")
    async def raise_error(error_type: str):
        match error_type:
            case "not_found":
                raise NotFoundError("resource X not found")
            case "permission":
                raise PermissionDeniedError("access denied")
            case "conflict":
                raise ConflictError("duplicate key")
            case "budget":
                raise BudgetExceededError("token budget exceeded")
            case "cancelled":
                raise CancelledError("run cancelled by user")
            case "timeout":
                raise TimeoutError("run timed out")
            case "llm":
                raise LLMProviderError("llm failed", error=None)  # type: ignore[arg-type]
            case "tool":
                raise ToolError("tool error", tool_name="test_tool")
            case "infra":
                raise InfraError("db connection failed")
            case "generic":
                raise AgentError("something went wrong")
            case _:
                return {"ok": True}

    return app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestErrorMapping:
    def test_error_map_coverage(self):
        assert NotFoundError in ERROR_MAP
        assert PermissionDeniedError in ERROR_MAP
        assert ConflictError in ERROR_MAP
        assert BudgetExceededError in ERROR_MAP
        assert CancelledError in ERROR_MAP
        assert TimeoutError in ERROR_MAP
        assert LLMProviderError in ERROR_MAP
        assert ToolError in ERROR_MAP
        assert InfraError in ERROR_MAP

    def test_not_found(self, client):
        r = client.get("/raise/not_found")
        assert r.status_code == 404
        assert r.json()["error"] == "not_found"

    def test_permission_denied(self, client):
        r = client.get("/raise/permission")
        assert r.status_code == 403
        assert r.json()["error"] == "permission_denied"

    def test_conflict(self, client):
        r = client.get("/raise/conflict")
        assert r.status_code == 409
        assert r.json()["error"] == "conflict"

    def test_budget_exceeded(self, client):
        r = client.get("/raise/budget")
        assert r.status_code == 429

    def test_timeout(self, client):
        r = client.get("/raise/timeout")
        assert r.status_code == 504

    def test_llm_provider_error(self, client):
        r = client.get("/raise/llm")
        assert r.status_code == 502

    def test_tool_error(self, client):
        r = client.get("/raise/tool")
        assert r.status_code == 502

    def test_infra_error(self, client):
        r = client.get("/raise/infra")
        assert r.status_code == 503

    def test_generic_agent_error(self, client):
        r = client.get("/raise/generic")
        assert r.status_code == 500
        assert r.json()["error"] == "internal"
