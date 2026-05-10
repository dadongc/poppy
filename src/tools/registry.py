from __future__ import annotations

import importlib
from pathlib import Path

from .protocol import Tool


class ToolRegistry:
    """工具注册表。管理内建工具 + 用户自定义工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    async def load_builtins(self) -> None:
        from src.tools.builtin.bash_exec import BashExecTool
        from src.tools.builtin.calculator import CalculatorTool
        from src.tools.builtin.datetime_tool import DateTimeTool
        from src.tools.builtin.delegate_task import DelegateTaskTool
        from src.tools.builtin.final_answer import FinalAnswerTool
        from src.tools.builtin.forget import ForgetTool
        from src.tools.builtin.list_skills import ListSkillsTool
        from src.tools.builtin.load_skill import LoadSkillTool
        from src.tools.builtin.python_exec import PythonExecTool
        from src.tools.builtin.read_artifact import ReadArtifactTool
        from src.tools.builtin.remember import RememberTool
        from src.tools.builtin.skill_install import SkillInstallTool
        from src.tools.builtin.web_fetch import WebFetchTool
        from src.tools.builtin.web_search import WebSearchTool

        for cls in [
            BashExecTool,
            CalculatorTool,
            DateTimeTool,
            FinalAnswerTool,
            ListSkillsTool,
            LoadSkillTool,
            DelegateTaskTool,
            PythonExecTool,
            ReadArtifactTool,
            RememberTool,
            ForgetTool,
            SkillInstallTool,
            WebFetchTool,
            WebSearchTool,
        ]:
            t: Tool = cls()  # type: ignore[assignment]
            self._tools[t.name] = t

    async def load_from_dir(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            return
        for f in p.rglob("*.py"):
            if f.name.startswith("_"):
                continue
            rel = f.relative_to(Path.cwd())
            mod_path = str(rel.with_suffix("")).replace("/", ".").replace("\\", ".")
            mod = importlib.import_module(mod_path)
            tool = getattr(mod, "TOOL", None)
            if tool:
                self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_for_agent(self, spec) -> list[Tool]:
        allowed = spec.allowed_tools if hasattr(spec, "allowed_tools") else set()
        if not allowed:
            return list(self._tools.values())
        return [t for n, t in self._tools.items() if n in allowed]

    def list_all(self) -> list[Tool]:
        return list(self._tools.values())
