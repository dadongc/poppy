from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from src.skills.registry import Skill, SkillRegistry
from src.tools.builtin.skill_install import SkillInstallTool


@pytest_asyncio.fixture
async def skill_registry():
    builtin = tempfile.mkdtemp()
    user = tempfile.mkdtemp()

    # Write a builtin skill
    (Path(builtin) / "greet.md").write_text(
        """---
name: greet
description: A greeting skill
version: "1.0"
---
# Greet
Say hello.
"""
    )

    # Write another builtin skill
    (Path(builtin) / "math.md").write_text(
        """---
name: math
description: Math helper
version: "1.0"
---
# Math
Do math.
"""
    )

    reg = SkillRegistry(skills_path=builtin, user_skills_path=user)
    await reg.load()
    yield reg

    import shutil

    shutil.rmtree(builtin, ignore_errors=True)
    shutil.rmtree(user, ignore_errors=True)


class TestSkillRegistryMultiPath:
    @pytest.mark.asyncio
    async def test_load_builtins(self, skill_registry):
        greet = await skill_registry.get("greet")
        assert greet is not None
        assert greet.source_kind == "builtin"
        assert greet.description == "A greeting skill"

    @pytest.mark.asyncio
    async def test_list_all(self, skill_registry):
        skills = await skill_registry.list()
        names = {s.name for s in skills}
        assert "greet" in names
        assert "math" in names

    @pytest.mark.asyncio
    async def test_install_user_skill(self, skill_registry):
        await skill_registry.install(
            "custom",
            """---
name: custom
description: A user-installed skill
version: "2.0"
---
# Custom
User content.
""",
        )
        s = await skill_registry.get("custom")
        assert s is not None
        assert s.source_kind == "user"
        assert s.description == "A user-installed skill"
        assert s.version == "2.0"

    @pytest.mark.asyncio
    async def test_user_overrides_builtin(self, skill_registry):
        # Install user skill with same name as builtin
        await skill_registry.install(
            "greet",
            """---
name: greet
description: Overridden greeting
version: "3.0"
---
# Greet
Overridden.
""",
        )
        s = await skill_registry.get("greet")
        assert s.source_kind == "user"
        assert s.description == "Overridden greeting"

    @pytest.mark.asyncio
    async def test_uninstall_falls_back_to_builtin(self, skill_registry):
        await skill_registry.install(
            "greet",
            """---
name: greet
description: Overridden
version: "3.0"
---
# Greet
Overridden.
""",
        )
        ok = await skill_registry.uninstall("greet")
        assert ok is True

        s = await skill_registry.get("greet")
        assert s is not None
        assert s.source_kind == "builtin"
        assert s.description == "A greeting skill"

    @pytest.mark.asyncio
    async def test_uninstall_builtin_fails(self, skill_registry):
        ok = await skill_registry.uninstall("greet")
        assert ok is False

    @pytest.mark.asyncio
    async def test_uninstall_nonexistent(self, skill_registry):
        ok = await skill_registry.uninstall("nonexistent")
        assert ok is False


class TestSkillInstallTool:
    @pytest.mark.asyncio
    async def test_invalid_url_scheme(self, agent_ctx):
        tool = SkillInstallTool()
        result = await tool.execute(agent_ctx, {"url": "ftp://example.com/skill.md"})
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_no_skill_registry(self, agent_ctx):
        agent_ctx.services.skill = None
        tool = SkillInstallTool()
        result = await tool.execute(
            agent_ctx, {"url": "https://example.com/skill.md"}
        )
        assert result.status == "error"
        assert "not available" in result.error_message
