from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.common.config import AppConfig, GatewayConfig
from src.gateway.deps import auth_user_id


class FakeRuntime:
    def __init__(self, api_key: str = ""):
        self._config = AppConfig(
            infra={"relational": {}, "vector": {}, "keyword": {}, "blob": {}, "cache": {}, "eventbus": {}},
            llm={"providers": {}, "default_model": ""},
            agent={"registry_path": "", "skills_path": ""},
            gateway=GatewayConfig(api_key=api_key),
            embedding={"provider": "", "model": "", "dim": 0},
        )


@pytest.fixture
def app_no_key():
    app = FastAPI()
    app.state.runtime = FakeRuntime(api_key="")

    @app.get("/protected")
    async def protected(user_id: str = Depends(auth_user_id)):
        return {"user_id": user_id}

    return app


@pytest.fixture
def app_with_key():
    app = FastAPI()
    app.state.runtime = FakeRuntime(api_key="secret-token")

    @app.get("/protected")
    async def protected(user_id: str = Depends(auth_user_id)):
        return {"user_id": user_id}

    return app


class TestAuth:
    def test_no_api_key_allows_any_token(self, app_no_key):
        client = TestClient(app_no_key)
        r = client.get("/protected", headers={"Authorization": "Bearer anything"})
        assert r.status_code == 200
        assert r.json()["user_id"] == "default"

    def test_no_auth_header_fails(self, app_no_key):
        client = TestClient(app_no_key)
        r = client.get("/protected")
        assert r.status_code == 422  # FastAPI validation error for missing header

    def test_invalid_prefix_fails(self, app_no_key):
        client = TestClient(app_no_key)
        r = client.get("/protected", headers={"Authorization": "Basic xyz"})
        assert r.status_code == 401

    def test_correct_token_succeeds(self, app_with_key):
        client = TestClient(app_with_key)
        r = client.get("/protected", headers={"Authorization": "Bearer secret-token"})
        assert r.status_code == 200
        assert r.json()["user_id"] == "default"

    def test_wrong_token_fails(self, app_with_key):
        client = TestClient(app_with_key)
        r = client.get("/protected", headers={"Authorization": "Bearer wrong-token"})
        assert r.status_code == 401
