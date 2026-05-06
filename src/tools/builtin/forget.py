from __future__ import annotations

from src.common.types import ToolResult


class ForgetTool:
    name = "forget"
    description = "删除一条记忆（软删除）。"
    schema = {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "记忆 ID"}
        },
        "required": ["memory_id"],
    }
    scopes: list[str] = ["memory.write"]
    is_builtin = True
    cacheable = False
    cache_ttl = 0

    async def execute(self, ctx, args):
        memory_svc = ctx.services.memory
        if memory_svc is None:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message="memory service not available",
            )
        await memory_svc.forget(args["memory_id"], ctx.user_id)
        return ToolResult(
            call_id="",
            name=self.name,
            status="ok",
            content="已删除。",
        )
