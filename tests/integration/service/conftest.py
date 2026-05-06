"""Integration test fixtures — require real PG/Redis/OSS infrastructure.

These tests are NOT run in the standard `pytest tests/` suite.
Use:  pytest tests/integration/ -v
"""
from __future__ import annotations

import pytest

from src.common.config import AppConfig, InfraConfig, load_config
from src.infra.factory import build_infra


@pytest.fixture
async def prod_cfg():
    cfg = load_config("config/prod.yaml")
    return cfg


@pytest.fixture
async def prod_infra(prod_cfg: AppConfig):
    infra = await build_infra(prod_cfg.infra.model_dump(), run_migrations_flag=True)
    yield infra
    await infra.relational.close()
    await infra.eventbus.shutdown()
