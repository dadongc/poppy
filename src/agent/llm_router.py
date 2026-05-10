from __future__ import annotations

from pathlib import Path

import yaml

from src.common.errors import ConfigError, NotFoundError

from .llm_providers.base import LLMProvider


class ModelRouter:
    """Model → provider 路由器。从 YAML catalog 加载模型清单。"""

    def __init__(
        self,
        catalog_path: str,
        providers: dict[str, LLMProvider],
    ) -> None:
        self.catalog: dict = self._load(catalog_path)
        self.providers = providers

    def resolve(self, model: str) -> tuple[LLMProvider, dict]:
        spec = self.catalog.get("models", {}).get(model)
        if not spec:
            raise NotFoundError(f"unknown model: {model}")
        provider_name = spec.get("provider", "")
        provider = self.providers.get(provider_name)
        if not provider:
            raise ConfigError(f"provider not configured: {provider_name}")
        return provider, spec

    @staticmethod
    def _load(path: str) -> dict:
        with open(Path(path)) as fp:
            return yaml.safe_load(fp) or {}
