from __future__ import annotations

from src.common.types import ToolResult


class ArtifactSaveTool:
    name = "artifact_save"
    description = "将文本内容保存为 Artifact 文件。用于保存日报、报告等输出。"
    schema = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "文件名，如 daily-digest/2026-05-23.md",
            },
            "content": {
                "type": "string",
                "description": "文件内容",
            },
            "content_type": {
                "type": "string",
                "default": "text/markdown",
                "description": "内容 MIME 类型",
            },
        },
        "required": ["name", "content"],
    }
    scopes: list[str] = []
    is_builtin = False
    cacheable = False
    cache_ttl = 0

    async def execute(self, ctx, args):
        artifact_svc = ctx.services.artifact
        if artifact_svc is None:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message="artifact service not available",
            )

        name = args["name"]
        content = args["content"]
        content_type = args.get("content_type", "text/markdown")

        try:
            artifact = await artifact_svc.save(
                user_id=ctx.user_id,
                content=content,
                mime_type=content_type,
                title=name,
                source_type="tool_output",
                source_run_id=ctx.run_id,
                source_tool_name=self.name,
            )
            return ToolResult(
                call_id="",
                name=self.name,
                status="ok",
                content=f"已保存: {name}",
                metadata={
                    "artifact_id": artifact.artifact_id,
                    "name": name,
                },
            )
        except Exception as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=str(e),
            )


TOOL = ArtifactSaveTool()
