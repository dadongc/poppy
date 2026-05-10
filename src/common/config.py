from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
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
    user_skills_path: str = "src/skills-user"
    max_sub_agent_depth: int = 3
    max_parallel_sub_agents: int = 5


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


def _resolve_env(value: str) -> str:
    """Resolve ${VAR} and ${VAR:-default} patterns in a string."""
    def _replace(m: re.Match) -> str:
        expr = m.group(1)
        if ":-" in expr:
            var, default = expr.split(":-", 1)
            v = os.environ.get(var.strip())
            return default.strip() if v is None else v
        v = os.environ.get(expr.strip())
        return m.group(0) if v is None else v

    return re.sub(r"\$\{([^}]+)\}", _replace, value)


def _walk(obj):
    """Recursively resolve env vars in all string values."""
    if isinstance(obj, dict):
        return {k: _walk(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v) for v in obj]
    if isinstance(obj, str):
        return _resolve_env(obj)
    return obj


def load_config(path: str | Path) -> AppConfig:
    load_dotenv()  # 自动加载项目根 .env
    with open(path) as f:
        raw: Any = yaml.safe_load(f)
    data = _walk(raw)
    return AppConfig(**data)  # pyright: ignore[reportCallIssue]
