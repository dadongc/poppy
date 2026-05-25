from __future__ import annotations

import json

import httpx

from src.common.types import ToolResult

API_BASE = "https://hacker-news.firebaseio.com/v0"
FETCH_TIMEOUT = 10.0


class HackerNewsTopTool:
    name = "hackernews_top"
    description = (
        "获取 Hacker News 当前热门文章列表。"
        "返回 top stories，支持按最低分数过滤。"
    )
    schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "default": 15,
                "description": "最多返回的文章数",
            },
            "min_score": {
                "type": "integer",
                "default": 50,
                "description": "最低分数阈值",
            },
        },
    }
    scopes: list[str] = []
    is_builtin = False
    cacheable = True
    cache_ttl = 300

    async def execute(self, ctx, args):
        limit = args.get("limit", 15)
        min_score = args.get("min_score", 50)

        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                # 1) 获取 top story IDs
                ids_resp = await client.get(f"{API_BASE}/topstories.json")
                ids_resp.raise_for_status()
                all_ids = ids_resp.json()

                # 2) 逐个获取 story 详情，直到凑够 limit 条
                entries: list[dict] = []
                for story_id in all_ids:
                    if len(entries) >= limit:
                        break
                    try:
                        item_resp = await client.get(
                            f"{API_BASE}/item/{story_id}.json"
                        )
                        item_resp.raise_for_status()
                        item = item_resp.json()
                        if item and item.get("score", 0) >= min_score and item.get("title"):
                            entries.append({
                                "title": item.get("title", ""),
                                "url": item.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                                "score": item.get("score", 0),
                                "descendants": item.get("descendants", 0),
                                "author": item.get("by", ""),
                            })
                    except Exception:
                        continue

            lines = [f"HN 热门: {len(entries)} 篇 (min_score={min_score})\n"]
            for e in entries:
                lines.append(
                    f"- [{e['title']}]({e['url']}) | score:{e['score']} | comments:{e['descendants']} | by:{e['author']}"
                )
            content = "\n".join(lines)

            # Best-effort 保存 artifact
            svc = ctx.services.artifact
            if svc and entries:
                try:
                    text = json.dumps(entries, ensure_ascii=False, indent=2)
                    await svc.save(
                        user_id=ctx.user_id,
                        title="hn-raw",
                        content=text,
                        mime_type="application/json",
                        source_tool_name="hackernews_top",
                    )
                except Exception:
                    pass

            return ToolResult(
                call_id="",
                name=self.name,
                status="ok",
                content=content,
                metadata={"entries_count": len(entries)},
            )
        except Exception as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=str(e),
            )


TOOL = HackerNewsTopTool()
