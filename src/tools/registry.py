from __future__ import annotations

import importlib
from pathlib import Path

from .protocol import Tool


class ToolRegistry:
    """工具注册表。所有工具启动时加载，构建上下文时按 builtin + allowed 过滤。"""

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
        """从目录加载所有工具到 _tools。"""
        p = Path(path).resolve()
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

    def list_for_agent(self, spec, extra_tools: set[str] | None = None) -> list[Tool]:
        allowed = set(spec.allowed_tools) if hasattr(spec, "allowed_tools") else set()
        if extra_tools:
            allowed = allowed | extra_tools
        if not allowed:
            return list(self._tools.values())
        # builtin 工具始终可见；custom 工具必须在 allowed 或 extra_tools 中
        denied = getattr(spec, "denied_tools", set()) or set()
        return [
            t for n, t in self._tools.items()
            if (n in allowed or t.is_builtin) and n not in denied
        ]

    def list_all(self) -> list[Tool]:
        return list(self._tools.values())
