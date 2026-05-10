from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from src.common.config import AppConfig, GatewayConfig, InfraConfig, LLMConfig, load_config


class TestInfraConfig:
    def test_from_dict(self):
        c = InfraConfig(
            relational={"driver": "sqlite"},
            vector={"driver": "sqlite-vec"},
            keyword={"driver": "fts5"},
            blob={"driver": "fs"},
            cache={"driver": "memory"},
            eventbus={"driver": "in_process"},
        )
        assert c.relational["driver"] == "sqlite"


class TestGatewayConfig:
    def test_defaults(self):
        c = GatewayConfig()
        assert c.host == "0.0.0.0"
        assert c.port == 8000
        assert c.api_key == ""

    def test_custom(self):
        c = GatewayConfig(host="127.0.0.1", port=9000)
        assert c.host == "127.0.0.1"
        assert c.port == 9000


class TestLLMConfig:
    def test_from_dict(self):
        c = LLMConfig(providers={"openai": {"key": "sk-xxx"}}, default_model="gpt-4o")
        assert c.default_model == "gpt-4o"


class TestLoadConfig:
    def test_from_yaml_file(self):
        data = {
            "infra": {
                "relational": {"driver": "sqlite"},
                "vector": {"driver": "none"},
                "keyword": {"driver": "none"},
                "blob": {"driver": "fs"},
                "cache": {"driver": "memory"},
                "eventbus": {"driver": "in_process"},
            },
            "llm": {"providers": {}, "default_model": "gpt-4o"},
            "agent": {"registry_path": "src/agents", "skills_path": "src/skills"},
            "gateway": {"host": "0.0.0.0", "port": 8000, "api_key": ""},
            "embedding": {"provider": "openai", "model": "text-embedding-3-small", "dim": 1536},
            "reranker": None,
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(data, f)
            tmp_path = f.name

        try:
            config = load_config(Path(tmp_path))
            assert isinstance(config, AppConfig)
            assert config.infra.relational["driver"] == "sqlite"
            assert config.llm.default_model == "gpt-4o"
            assert config.gateway.port == 8000
        finally:
            Path(tmp_path).unlink()
