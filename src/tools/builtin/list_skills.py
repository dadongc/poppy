from __future__ import annotations

from src.common.types import ToolResult


class ListSkillsTool:
    name = "list_skills"
    description = "列出所有可用技能及其描述。用于发现和了解可用的 skill，再通过 load_skill 加载需要的。"
    schema = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["all", "builtin", "user"],
                "description": "筛选类型：all=全部, builtin=内置, user=用户安装",
            },
        },
        "required": [],
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
                error_message="skill registry not available",
            )

        kind_filter = args.get("kind", "all")
        skills = await skill_svc.list()

        if kind_filter != "all":
            skills = [s for s in skills if s.source_kind == kind_filter]

        if not skills:
            return ToolResult(
                call_id="",
                name=self.name,
                status="ok",
                content="(没有可用的 skill)",
            )

        lines = []
        for s in skills:
            loaded = " [已加载]" if ctx.extra_inputs.get("loaded_skills") and any(
                ls.name == s.name if hasattr(ls, "name") else False
                for ls in ctx.extra_inputs.get("loaded_skills", [])
            ) else ""
            source = "内置" if s.source_kind == "builtin" else "用户"
            lines.append(
                f"- **{s.name}** [{source}] v{s.version}{loaded}: {s.description}"
            )
            if s.required_tools:
                lines.append(f"  需要的工具: {', '.join(s.required_tools)}")

        return ToolResult(
            call_id="",
            name=self.name,
            status="ok",
            content="\n".join(lines),
            metadata={
                "total": len(skills),
                "skills": [{  "name": s.name, "description": s.description,
                    "source_kind": s.source_kind, "version": s.version,
                } for s in skills],
            },
        )
