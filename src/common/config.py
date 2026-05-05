from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class InfraConfig(BaseModel):
    relational: dict
    vector: dict
    keyword: dict
    blob: dict
    cache: dict
    eventbus: dict


class LLMConfig(BaseModel):
    providers: dict
    default_model: str


class AgentConfig(BaseModel):
    registry_path: str
    skills_path: str


class GatewayConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    api_key: str = ""
    cors_origins: list[str] = []


class AppConfig(BaseModel):
    infra: InfraConfig
    llm: LLMConfig
    agent: AgentConfig
    gateway: GatewayConfig
    embedding: dict
    reranker: dict | None = None


def load_config(path: str | Path) -> AppConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return AppConfig(**data)
