from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(slots=True)
class SkillProfile:
    """子 Agent 派生时的行为配置。"""
    max_steps: int = 5
    token_budget: int = 10000
    deadline_sec: int = 60
    preferred_model: str = ""
    temperature: float = 0.7
    system_prompt_suffix: str = ""
    output_schema: dict | None = None


@dataclass(slots=True)
class Skill:
    name: str
    display_name: str = ""
    description: str = ""
    version: str = "1.0"
    author: str = ""
    content: str = ""
    source_path: str = ""
    source_kind: str = "builtin"  # builtin | user
    required_tools: list[str] = field(default_factory=list)
    optional_tools: list[str] = field(default_factory=list)
    preferred_mode: str = "auto"  # auto | in_context | delegated
    default_max_steps: int = 6
    instructions: str = ""
    agent_profile: SkillProfile = field(default_factory=SkillProfile)
    triggers: dict = field(default_factory=dict)
    resources: list[str] = field(default_factory=list)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown text. Returns (meta, body)."""
    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        meta = {}
    return meta, parts[2].strip()


def _build_skill(raw: str, source_path: str, source_kind: str) -> Skill:
    meta, body = _parse_frontmatter(raw)

    desc = meta.get("description", "")
    if not desc:
        for line in body.split("\n"):
            if line.startswith("# "):
                desc = line.lstrip("# ").strip()
                break

    profile_data = meta.get("agent_profile", {})
    profile = SkillProfile(
        max_steps=profile_data.get("max_steps", 5),
        token_budget=profile_data.get("token_budget", 10000),
        deadline_sec=profile_data.get("deadline_sec", 60),
        preferred_model=profile_data.get("preferred_model", ""),
        temperature=profile_data.get("temperature", 0.7),
        system_prompt_suffix=profile_data.get("system_prompt_suffix", ""),
        output_schema=profile_data.get("output_schema"),
    )

    triggers = meta.get("triggers", {})
    if not isinstance(triggers, dict):
        triggers = {}

    return Skill(
        name=meta.get("name", Path(source_path).stem),
        display_name=meta.get("display_name", ""),
        description=desc,
        version=str(meta.get("version", "1.0")),
        author=meta.get("author", ""),
        content=raw,
        source_path=source_path,
        source_kind=source_kind,
        required_tools=meta.get("required_tools", []),
        optional_tools=meta.get("optional_tools", []),
        preferred_mode=meta.get("preferred_mode", "auto"),
        default_max_steps=meta.get("default_max_steps", 6),
        instructions=body,
        agent_profile=profile,
        triggers=triggers,
        resources=meta.get("resources", []),
    )


class SkillRegistry:
    """技能注册表。从内置路径 + 用户路径加载 .md 文件，用户 skill 覆盖同名内置 skill。"""

    def __init__(self, skills_path: str, user_skills_path: str = "skills") -> None:
        self._builtin_path = Path(skills_path)
        self._user_path = Path(user_skills_path)
        self._skills: dict[str, Skill] = {}

    @property
    def user_path(self) -> Path:
        return self._user_path

    async def load(self) -> None:
        self._skills.clear()

        # 1) Load builtin skills
        await self._load_from_dir(self._builtin_path, "builtin")

        # 2) Load user skills (override builtin with same name)
        await self._load_from_dir(self._user_path, "user")

    async def _load_from_dir(self, path: Path, kind: str) -> None:
        if not path.exists():
            return
        for f in path.glob("*.md"):
            name = f.stem
            raw = f.read_text(encoding="utf-8")
            self._skills[name] = _build_skill(raw, str(f.absolute()), kind)

    async def install(self, name: str, content: str) -> Skill:
        """安装一个 skill 到用户目录。如果已存在则覆盖。"""
        self._user_path.mkdir(parents=True, exist_ok=True)
        file_path = self._user_path / f"{name}.md"
        file_path.write_text(content, encoding="utf-8")
        skill = _build_skill(content, str(file_path.absolute()), "user")
        skill.name = name
        self._skills[name] = skill
        return skill

    async def uninstall(self, name: str) -> bool:
        """卸载一个用户 skill。内置 skill 不可卸载。卸载后如有同名内置 skill 自动恢复。"""
        skill = self._skills.get(name)
        if skill is None:
            return False
        if skill.source_kind == "builtin":
            return False
        file_path = Path(skill.source_path)
        if file_path.exists():
            file_path.unlink()
        self._skills.pop(name, None)

        # Fall back to builtin if exists
        builtin_file = self._builtin_path / f"{name}.md"
        if builtin_file.exists():
            raw = builtin_file.read_text(encoding="utf-8")
            self._skills[name] = _build_skill(raw, str(builtin_file.absolute()), "builtin")

        return True

    async def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    async def list(self) -> list[Skill]:
        return list(self._skills.values())
