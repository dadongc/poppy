from __future__ import annotations

import pytest

from src.common.config import AgentConfig, AppConfig, GatewayConfig, InfraConfig, LLMConfig
from src.infra.factory import build_infra
from src.service.container import build_services


@pytest.fixture
def dev_cfg():
    return AppConfig(
        infra=InfraConfig(
            relational={"backend": "sqlite", "path": ":memory:"},
            vector={"backend": "sqlite-vec", "dim": 4},
            keyword={"backend": "fts5"},
            blob={"backend": "filesystem", "root": "/tmp"},
            cache={"backend": "memory", "max_size": 100},
            eventbus={"backend": "inproc", "persist": False},
        ),
        llm=LLMConfig(providers={}, default_model=""),
        agent=AgentConfig(registry_path="src/agents", skills_path="src/skills"),
        gateway=GatewayConfig(),
        embedding={"provider": "stub", "model": "stub", "dim": 4},
    )


class TestBuildServices:
    async def test_build_all_services(self, dev_cfg):
        infra = await build_infra(dev_cfg.infra.model_dump(), run_migrations_flag=False)
        try:
            services = await build_services(infra, cfg=dev_cfg)
            assert services.session is not None
            assert services.memory is not None
            assert services.artifact is not None
            assert services.kb is not None
            assert services.retriever is not None
            assert services.embedding is not None
        finally:
            await infra.relational.close()
            await infra.eventbus.shutdown()

    async def test_services_wired_correctly(self, dev_cfg):
        infra = await build_infra(dev_cfg.infra.model_dump(), run_migrations_flag=False)
        try:
            services = await build_services(infra, cfg=dev_cfg)
            assert services.session._store is infra.relational
            assert services.artifact._blob is infra.blob
            assert services.kb._vector is infra.vector
            assert services.memory._vector is infra.vector
        finally:
            await infra.relational.close()
            await infra.eventbus.shutdown()
