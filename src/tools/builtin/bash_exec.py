from __future__ import annotations

import asyncio
import re

from src.common.types import ToolResult

EXEC_TIMEOUT = 30  # seconds
MAX_OUTPUT_CHARS = 8192

# Patterns that trigger a safety block
_DENY_PATTERNS = [
    re.compile(p) for p in [
        r"rm\s+(-[rRf]+\s+)*/",
        r"sudo\b",
        r"mkfs\.",
        r"dd\s+if=",
        r":\(\)\s*\{",  # fork bomb
        r">\s*/dev/sd",
        r"chmod\s+.*777",
        r"curl.*\|\s*(ba)?sh",
        r"wget.*\|\s*(ba)?sh",
    ]
]


class BashExecTool:
    name = "bash_exec"
    description = (
        "在隔离子进程中执行 bash 命令。超时 30 秒，输出限制 8192 字符。"
        "禁止执行危险命令（如 rm -rf /、sudo、fork bomb 等）。"
        "适合文件操作、数据处理、系统查询等场景。"
    )
    schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 bash 命令",
            },
            "workdir": {
                "type": "string",
                "description": "工作目录（可选，默认为当前目录）",
            },
        },
        "required": ["command"],
    }
    scopes: list[str] = []
    is_builtin = True
    cacheable = False
    cache_ttl = 0

    async def execute(self, ctx, args):
        command = args["command"]
        workdir = args.get("workdir", "")

        for pattern in _DENY_PATTERNS:
            if pattern.search(command):
                return ToolResult(
                    call_id="",
                    name=self.name,
                    status="error",
                    error_message=f"危险命令被阻止: 匹配模式 {pattern.pattern!r}",
                )

        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir or None,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=EXEC_TIMEOUT
            )

            out_text = stdout.decode("utf-8", errors="replace")
            err_text = stderr.decode("utf-8", errors="replace")

            parts: list[str] = []
            if out_text:
                if len(out_text) > MAX_OUTPUT_CHARS:
                    out_text = out_text[:MAX_OUTPUT_CHARS] + "\n...[stdout truncated]"
                parts.append(out_text.rstrip())
            if err_text:
                if len(err_text) > 500:
                    err_text = err_text[:500] + "\n...[stderr truncated]"
                parts.append(f"[stderr]\n{err_text.rstrip()}")

            content = "\n".join(parts).strip() or "(no output)"

            is_error = proc.returncode is not None and proc.returncode != 0
            return ToolResult(
                call_id="",
                name=self.name,
                status="error" if is_error else "ok",
                content=content,
                metadata={"exit_code": proc.returncode},
            )

        except TimeoutError:
            return ToolResult(
                call_id="",
                name=self.name,
                status="timeout",
                error_message=f"execution timed out after {EXEC_TIMEOUT}s",
            )
        except Exception as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=str(e),
            )
