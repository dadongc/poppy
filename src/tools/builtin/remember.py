from __future__ import annotations

from src.common.types import ToolResult


class RememberTool:
    name = "remember"
    description = "把一条信息显式写入用户长期记忆。"
    schema = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": [
                    "profile", "preference", "fact", "event",
                    "episode", "task", "reminder", "relation",
                ],
            },
            "content": {"type": "string"},
            "importance": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "default": 0.6,
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["kind", "content"],
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
        rec = await memory_svc.remember(
            user_id=ctx.user_id,
            kind=args["kind"],
            content=args["content"],
            importance=args.get("importance", 0.6),
            tags=args.get("tags"),
            source_type="explicit",
            source_run_id=ctx.run_id,
            source_session_id=ctx.session_id,
        )
        return ToolResult(
            call_id="",
            name=self.name,
            status="ok",
            content=f"[{rec.kind}] {rec.content}",
            metadata={"memory_id": rec.memory_id},
        )
