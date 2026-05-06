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


class SkillRegistry:
    """技能注册表。从 skills_path 目录加载 .md 文件，支持 YAML frontmatter。"""

    def __init__(self, skills_path: str) -> None:
        self._path = Path(skills_path)
        self._skills: dict[str, Skill] = {}

    async def load(self) -> None:
        if not self._path.exists():
            return
        self._skills.clear()
        for f in self._path.glob("*.md"):
            name = f.stem
            raw = f.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(raw)

            # description: YAML frontmatter 优先, 否则取正文第一个 h1
            desc = meta.get("description", "")
            if not desc:
                for line in body.split("\n"):
                    if line.startswith("# "):
                        desc = line.lstrip("# ").strip()
                        break

            # --- agent_profile ---
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

            # --- triggers ---
            triggers = meta.get("triggers", {})
            if not isinstance(triggers, dict):
                triggers = {}

            self._skills[name] = Skill(
                name=meta.get("name", name),
                display_name=meta.get("display_name", ""),
                description=desc,
                version=str(meta.get("version", "1.0")),
                author=meta.get("author", ""),
                content=raw,
                source_path=str(f.absolute()),
                required_tools=meta.get("required_tools", []),
                optional_tools=meta.get("optional_tools", []),
                preferred_mode=meta.get("preferred_mode", "auto"),
                default_max_steps=meta.get("default_max_steps", 6),
                instructions=body,
                agent_profile=profile,
                triggers=triggers,
                resources=meta.get("resources", []),
            )

    async def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    async def list(self) -> list[Skill]:
        return list(self._skills.values())
