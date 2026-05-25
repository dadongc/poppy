from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from src.common.types import ToolResult

FETCH_TIMEOUT = 15.0


def _struct_time_to_iso(ts) -> str:
    """将 feedparser 返回的 time.struct_time 转为 ISO 8601 字符串。"""
    import time as _time

    try:
        return _time.strftime("%Y-%m-%dT%H:%M:%S", ts)
    except Exception:
        return ""


def _parse_feed(raw: bytes) -> list[dict[str, Any]]:
    """解析 RSS/Atom 原始字节，返回条目列表。"""
    import feedparser

    feed = feedparser.parse(raw)
    entries: list[dict[str, Any]] = []
    for e in feed.entries:
        published = e.get("published_parsed") or e.get("updated_parsed")
        entries.append({
            "title": e.get("title", ""),
            "url": e.get("link", ""),
            "author": e.get("author", ""),
            "summary": _strip_markup(e.get("summary", "") or e.get("description", "")),
            "published_at": _struct_time_to_iso(published) if published else "",
        })
    return entries


def _strip_markup(text: str, max_len: int = 300) -> str:
    """去除 HTML 标签，截断。"""
    import re

    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text


class RssFetchTool:
    name = "rss_fetch"
    description = (
        "并发抓取多个 RSS/Atom 订阅源，返回文章列表。"
        "每个源独立超时，单个失败不影响其他源。"
    )
    schema = {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "RSS/Atom 订阅源 URL 列表",
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "每个源最多返回的文章数",
            },
        },
        "required": ["urls"],
    }
    scopes: list[str] = []
    is_builtin = False
    cacheable = True
    cache_ttl = 600

    async def execute(self, ctx, args):
        urls = args["urls"]
        limit = args.get("limit", 20)

        async def fetch_one(url: str) -> tuple[str, list[dict], str | None]:
            try:
                async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                    resp = await client.get(
                        url,
                        headers={
                            "User-Agent": "Poppy/1.0",
                            "Accept": "application/rss+xml, application/atom+xml, text/xml, application/xml, */*",
                        },
                        follow_redirects=True,
                    )
                    resp.raise_for_status()
                    entries = _parse_feed(resp.content)[:limit]
                    return url, entries, None
            except httpx.TimeoutException:
                return url, [], f"timeout after {FETCH_TIMEOUT}s"
            except Exception as e:
                return url, [], str(e)

        results = await asyncio.gather(*(fetch_one(u) for u in urls))

        entries: list[dict] = []
        errors: list[dict] = []
        for url, items, err in results:
            if err:
                errors.append({"url": url, "error": err})
            for item in items:
                item["source_url"] = url
                entries.append(item)

        lines = [f"共抓取 {len(entries)} 篇文章，{len(errors)} 个源失败\n"]
        for e in entries:
            lines.append(f"- [{e['title']}]({e['url']}) | {e.get('author', '')} | {e['summary'][:200]}")
        content = "\n".join(lines)

        # Best-effort 保存 artifact
        svc = ctx.services.artifact
        if svc and entries:
            try:
                text = json.dumps(entries, ensure_ascii=False, indent=2)
                await svc.save(
                    user_id=ctx.user_id,
                    title="rss-raw",
                    content=text,
                    mime_type="application/json",
                    source_tool_name="rss_fetch",
                )
            except Exception:
                pass

        return ToolResult(
            call_id="",
            name=self.name,
            status="ok",
            content=content,
            metadata={"entries_count": len(entries), "failed_count": len(errors)},
        )


TOOL = RssFetchTool()
