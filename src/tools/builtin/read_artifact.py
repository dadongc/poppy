from __future__ import annotations

from src.common.errors import NotFoundError
from src.common.types import ToolResult


class ReadArtifactTool:
    name = "read_artifact"
    description = "读取一个 artifact 的完整文本内容。"
    schema = {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string", "description": "Artifact ID"},
            "max_chars": {
                "type": "integer",
                "default": 20000,
                "description": "返回内容最大字符数，超出截断",
            },
        },
        "required": ["artifact_id"],
    }
    scopes: list[str] = []
    is_builtin = True
    cacheable = True
    cache_ttl = 300

    async def execute(self, ctx, args):
        artifact_svc = ctx.services.artifact
        if artifact_svc is None:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message="artifact service not available",
            )
        try:
            text = await artifact_svc.get_text(args["artifact_id"], ctx.user_id)
            limit = args.get("max_chars", 20000)
            truncated = len(text) > limit
            content = text[:limit] + ("\n...[truncated]" if truncated else "")
            return ToolResult(
                call_id="",
                name=self.name,
                status="ok",
                content=content,
            )
        except NotFoundError as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_type="NotFound",
                error_message=str(e),
            )
