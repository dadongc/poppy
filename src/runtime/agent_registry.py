from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from src.common.types import AgentSpec


def _import_class(cls_path: str):
    mod_path, cls_name = cls_path.rsplit(".", 1)
    import importlib

    mod = importlib.import_module(mod_path)
    return getattr(mod, cls_name)


class AgentRegistry:
    """AgentSpec 注册表。从 YAML 文件加载，支持 mtime 热更新。"""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._cache: dict[str, AgentSpec] = {}
        self._mtime: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        async with self._lock:
            self._cache.clear()
            self._mtime.clear()
            if not self._path.exists():
                return
            for f in self._path.glob("*.yaml"):
                spec = self._load_file(f)
                self._cache[spec.name] = spec
                self._mtime[spec.name] = f.stat().st_mtime

    async def resolve(self, name: str) -> AgentSpec | None:
        async with self._lock:
            spec = self._cache.get(name)
            if spec and spec.source_path:
                f = Path(spec.source_path)
                if f.exists() and f.stat().st_mtime > self._mtime.get(name, 0):
                    new_spec = self._load_file(f)
                    self._cache[name] = new_spec
                    self._mtime[name] = f.stat().st_mtime
                    return new_spec
            return spec

    async def list(self) -> list[AgentSpec]:
        async with self._lock:
            return list(self._cache.values())

    async def register(self, spec: AgentSpec) -> None:
        async with self._lock:
            self._cache[spec.name] = spec

    def _load_file(self, f: Path) -> AgentSpec:
        with open(f) as fp:
            data = yaml.safe_load(fp)
        agent_fields = {k: v for k, v in data.items() if k != "cls"}
        return AgentSpec(
            source="registry",
            source_path=str(f.absolute()),
            mtime=f.stat().st_mtime,
            **agent_fields,
        )
