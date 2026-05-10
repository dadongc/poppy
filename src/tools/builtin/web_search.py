from __future__ import annotations

from src.common.types import ToolResult

MAX_RESULTS = 10


class WebSearchTool:
    name = "web_search"
    description = (
        "在互联网上搜索信息。返回标题、URL 和摘要列表。"
        "适用于需要获取最新信息或核实事实的场景。"
    )
    schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "max_results": {
                "type": "integer",
                "default": 5,
                "description": "返回结果的最大数量（默认 5，最多 10）",
            },
            "time_limit": {
                "type": "string",
                "enum": ["d", "w", "m", "y"],
                "description": "时间限制: d=一天, w=一周, m=一月, y=一年",
            },
        },
        "required": ["query"],
    }
    scopes: list[str] = []
    is_builtin = True
    cacheable = True
    cache_ttl = 300

    async def execute(self, ctx, args):
        query = args["query"]
        max_results = min(args.get("max_results", 5), MAX_RESULTS)
        time_limit = args.get("time_limit")

        try:
            from duckduckgo_search import DDGS

            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(
                    keywords=query,
                    max_results=max_results,
                    timelimit=time_limit,
                ):
                    results.append(
                        {
                            "title": r.get("title", ""),
                            "href": r.get("href", ""),
                            "body": r.get("body", ""),
                        }
                    )

            if not results:
                return ToolResult(
                    call_id="",
                    name=self.name,
                    status="ok",
                    content="未找到相关搜索结果。",
                )

            lines = []
            for i, r in enumerate(results):
                lines.append(f"{i + 1}. {r['title']}")
                lines.append(f"   URL: {r['href']}")
                lines.append(f"   {r['body']}")
                lines.append("")

            return ToolResult(
                call_id="",
                name=self.name,
                status="ok",
                content="\n".join(lines).strip(),
                metadata={"results": results},
            )
        except Exception as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=str(e),
            )
