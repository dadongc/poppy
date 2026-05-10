from __future__ import annotations

import os

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
            "search_depth": {
                "type": "string",
                "enum": ["basic", "advanced"],
                "description": "搜索深度: basic=快速, advanced=深入（默认 basic）",
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
        search_depth = args.get("search_depth", "basic")

        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message="TAVILY_API_KEY not configured. Set it in .env or environment.",
            )

        try:
            from tavily import TavilyClient

            client = TavilyClient(api_key=api_key)
            response = client.search(
                query=query,
                max_results=max_results,
                search_depth=search_depth,
                include_answer=search_depth == "advanced",
            )

            results = response.get("results", [])
            if not results:
                return ToolResult(
                    call_id="",
                    name=self.name,
                    status="ok",
                    content="未找到相关搜索结果。",
                )

            lines = []
            answer = response.get("answer", "")
            if answer:
                lines.append(f"**摘要**: {answer}")
                lines.append("")

            for i, r in enumerate(results):
                lines.append(f"{i + 1}. {r.get('title', '')}")
                lines.append(f"   URL: {r.get('url', '')}")
                content = r.get("content", "")
                lines.append(f"   {content}")
                lines.append("")

            return ToolResult(
                call_id="",
                name=self.name,
                status="ok",
                content="\n".join(lines).strip(),
                metadata={
                    "results": [
                        {"title": r.get("title", ""), "url": r.get("url", ""),
                         "content": r.get("content", ""), "score": r.get("score", 0)}
                        for r in results
                    ],
                    "answer": answer,
                    "response_time": response.get("response_time", 0),
                },
            )
        except Exception as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=str(e),
            )
