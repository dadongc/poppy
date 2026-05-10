from __future__ import annotations

import asyncio

from src.common.types import ToolResult

EXEC_TIMEOUT = 10  # seconds
MAX_OUTPUT_CHARS = 4096


# Blocklist of dangerous builtins that are removed from the sandbox
_BLOCKED_BUILTINS = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "open",
    "input",
    "breakpoint",
    "help",
    "memoryview",
}


class PythonExecTool:
    name = "python_exec"
    description = (
        "在隔离沙箱中执行 Python 代码。支持基础计算和数据处理。"
        "超时 10 秒，输出限制 4096 字符。禁止文件系统和网络访问。"
    )
    schema = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码。使用 print() 输出结果。",
            },
        },
        "required": ["code"],
    }
    scopes: list[str] = []
    is_builtin = True
    cacheable = False
    cache_ttl = 0

    async def execute(self, ctx, args):
        code = args["code"]

        # Build a sandboxed script that:
        # 1. Restricts builtins
        # 2. Captures stdout/stderr
        # 3. Has a timeout
        sandbox_code = _build_sandbox(code)

        try:
            proc = await asyncio.create_subprocess_exec(
                "python3",
                "-c",
                sandbox_code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=EXEC_TIMEOUT
            )

            out_text = stdout.decode("utf-8", errors="replace")
            err_text = stderr.decode("utf-8", errors="replace")

            if err_text:
                return ToolResult(
                    call_id="",
                    name=self.name,
                    status="error",
                    content=out_text,
                    error_message=err_text[:500],
                )

            if len(out_text) > MAX_OUTPUT_CHARS:
                out_text = out_text[:MAX_OUTPUT_CHARS] + "\n...[truncated]"

            return ToolResult(
                call_id="",
                name=self.name,
                status="ok",
                content=out_text.strip() or "(no output)",
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


def _build_sandbox(user_code: str) -> str:
    """Wrap user code in a sandbox that restricts dangerous operations."""
    blocked = ", ".join(repr(b) for b in _BLOCKED_BUILTINS)
    return f"""
import sys
import builtins

_blocked = {{{blocked}}}
for _name in _blocked:
    if hasattr(builtins, _name):
        setattr(builtins, _name, None)

class _SandboxedIO:
    def write(self, s): sys.__stdout__.write(s)
    def flush(self): sys.__stdout__.flush()
    def read(self, *a, **kw): raise OSError("input disabled")
    def readline(self, *a, **kw): raise OSError("input disabled")

sys.stdout = _SandboxedIO()
sys.stderr = _SandboxedIO()

try:
{_indent(user_code, 4)}
except Exception as _e:
    print(f"{{type(_e).__name__}}: {{_e}}", file=sys.__stderr__)
"""


def _indent(code: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line for line in code.split("\n"))
