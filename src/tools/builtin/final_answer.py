from __future__ import annotations


class FinalAnswerTool:
    name = "final_answer"
    description = "提供最终回答给用户。调用此工具会结束当前 Agent 执行。"
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "给用户看的最终答复"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["answer"],
    }
    scopes: list[str] = []
    is_builtin = True
    cacheable = False
    cache_ttl = 0

    async def execute(self, ctx, args):
        return args.get("answer", "")
