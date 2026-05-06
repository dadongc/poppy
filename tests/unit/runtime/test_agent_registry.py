from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from src.common.types import AgentSpec
from src.runtime.agent_registry import AgentRegistry


class TestAgentRegistry:
    @pytest.mark.asyncio
    async def test_load_from_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent_yaml = {
                "name": "test-agent",
                "description": "A test agent",
                "preferred_model": "deepseek-chat",
                "max_tokens": 4096,
                "allowed_tools": ["final_answer"],
                "max_steps": 10,
                "token_budget": 50000,
                "deadline_sec": 300,
            }
            f = Path(tmp) / "test-agent.yaml"
            f.write_text(yaml.dump(agent_yaml))

            reg = AgentRegistry(path=tmp)
            await reg.load()
            spec = await reg.resolve("test-agent")
            assert spec is not None
            assert spec.name == "test-agent"
            assert spec.preferred_model == "deepseek-chat"
            assert "final_answer" in spec.allowed_tools

    @pytest.mark.asyncio
    async def test_load_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = AgentRegistry(path=tmp)
            await reg.load()
            specs = await reg.list()
            assert specs == []

    @pytest.mark.asyncio
    async def test_resolve_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = AgentRegistry(path=tmp)
            await reg.load()
            assert await reg.resolve("nonexistent") is None

    @pytest.mark.asyncio
    async def test_hot_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent_yaml = {
                "name": "test-agent",
                "preferred_model": "deepseek-chat",
                "max_tokens": 4096,
                "allowed_tools": ["final_answer"],
                "max_steps": 10,
                "token_budget": 50000,
                "deadline_sec": 300,
            }
            f = Path(tmp) / "test-agent.yaml"
            f.write_text(yaml.dump(agent_yaml))

            reg = AgentRegistry(path=tmp)
            await reg.load()
            spec = await reg.resolve("test-agent")
            assert spec.preferred_model == "deepseek-chat"

            # Modify on disk
            agent_yaml["preferred_model"] = "gpt-4o"
            f.write_text(yaml.dump(agent_yaml))

            # Resolve should pick up the change
            spec2 = await reg.resolve("test-agent")
            assert spec2.preferred_model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_register_programmatic(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = AgentRegistry(path=tmp)
            await reg.load()
            spec = AgentSpec(
                name="dynamic-agent",
                preferred_model="deepseek-chat",
                max_tokens=2048,
                allowed_tools={"final_answer"},
                max_steps=5,
                token_budget=10000,
                deadline_sec=120,
            )
            await reg.register(spec)
            resolved = await reg.resolve("dynamic-agent")
            assert resolved is not None
            assert resolved.max_tokens == 2048

    @pytest.mark.asyncio
    async def test_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("agent-a", "agent-b"):
                data = {
                    "name": name,
                    "preferred_model": "deepseek-chat",
                    "max_tokens": 4096,
                    "allowed_tools": ["final_answer"],
                    "max_steps": 10,
                    "token_budget": 50000,
                    "deadline_sec": 300,
                }
                (Path(tmp) / f"{name}.yaml").write_text(yaml.dump(data))

            reg = AgentRegistry(path=tmp)
            await reg.load()
            specs = await reg.list()
            names = {s.name for s in specs}
            assert names == {"agent-a", "agent-b"}
