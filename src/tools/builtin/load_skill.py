from __future__ import annotations

from src.common.types import ToolResult


class LoadSkillTool:
    name = "load_skill"
    description = "加载指定技能。下一轮 prompt 会包含该技能的详细说明文档。"
    schema = {
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "技能名称"}
        },
        "required": ["skill_name"],
    }
    scopes: list[str] = []
    is_builtin = True
    cacheable = False
    cache_ttl = 0

    async def execute(self, ctx, args):
        skill_svc = ctx.services.skill
        if skill_svc is None:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message="skill service not available",
            )
        skill = await skill_svc.get(args["skill_name"])
        if not skill:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=f"skill not found: {args['skill_name']}",
            )
        loaded: list = ctx.extra_inputs.setdefault("loaded_skills", [])
        if skill not in loaded:
            loaded.append(skill)

        # 记录 skill 声明的 custom tools，context builder 和权限检查会用到
        all_tools = set(skill.required_tools) | set(skill.optional_tools)
        active: set = ctx.extra_inputs.setdefault("active_custom_tools", set())
        active.update(all_tools)

        return ToolResult(
            call_id="",
            name=self.name,
            status="ok",
            content=f"已加载技能 '{skill.name}'。",
        )
