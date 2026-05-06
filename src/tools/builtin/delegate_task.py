from __future__ import annotations

from src.common.types import ToolResult


class DelegateTaskTool:
    name = "delegate_task"
    description = (
        "派发子任务给 SubAgent。支持两种模式："
        "1) agent_type — 使用预注册的 Agent 类型；"
        "2) skills — 动态组装技能列表创建临时 Agent。"
        "同步等待子 Agent 完成并返回结果。"
    )
    schema = {
        "type": "object",
        "properties": {
            "agent_type": {
                "type": "string",
                "description": "预注册的子 Agent 类型名称（与 skills 二选一）",
            },
            "skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "动态组装技能列表（与 agent_type 二选一）",
            },
            "task": {"type": "string", "description": "任务描述"},
            "token_budget": {"type": "integer", "default": 20000},
            "deadline_sec": {"type": "integer", "default": 120},
        },
        "required": ["task"],
    }
    scopes: list[str] = []
    is_builtin = True
    cacheable = False
    cache_ttl = 0

    async def execute(self, ctx, args):
        orch = ctx.extra_inputs.get("_orch")
        if orch is None:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_type="NotAvailable",
                error_message="orchestrator not available for subagent spawning",
            )
        try:
            result_msg = await orch.spawn_subagent(
                agent_type=args.get("agent_type"),
                skills=args.get("skills"),
                task=args["task"],
                token_budget=args.get("token_budget"),
                deadline_sec=args.get("deadline_sec"),
            )
            return ToolResult(
                call_id="",
                name=self.name,
                status="ok",
                content=result_msg.content if hasattr(result_msg, "content") else str(result_msg),
            )
        except Exception as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_type=type(e).__name__,
                error_message=str(e),
            )
