from __future__ import annotations

import json
import re

import httpx

from src.common.types import ToolResult

TRENDING_URL = "https://github.com/trending"
FETCH_TIMEOUT = 10.0


def _parse_trending(html: str) -> list[dict]:
    """解析 GitHub Trending 页面。每篇文章包含仓库名、描述、语言、总 star、今日增量。"""
    results: list[dict] = []
    blocks = html.split('<article class="Box-row"')

    for block in blocks[1:]:
        # 仓库名: /owner/name
        name_m = re.search(r'href="/([^/]+/[^/"]+)"', block)
        if not name_m:
            continue
        name = name_m.group(1)
        if "/" not in name or name.count("/") > 1:
            continue
        # 过滤 sponsor 卡片
        if name.startswith("sponsors/"):
            continue

        # 描述
        desc = ""
        desc_m = re.search(r'<p class="col-9[^"]*">(.*?)</p>', block, re.DOTALL)
        if desc_m:
            desc = re.sub(r"<[^>]+>", "", desc_m.group(1)).strip()
            desc = re.sub(r"\s+", " ", desc)

        # 语言
        lang = ""
        lang_m = re.search(r'programmingLanguage">([^<]+)<', block)
        if lang_m:
            lang = lang_m.group(1).strip()

        # 总 star（number inside a link, like <a ...>1,234</a>）
        total_stars = 0
        star_m = re.search(r">\s*(\d[\d,]+)\s*</a>", block)
        if star_m:
            total_stars = int(star_m.group(1).replace(",", ""))

        # 今日增量
        stars_today = 0
        today_m = re.search(r"(\d[\d,]*)\s*stars today", block)
        if today_m:
            stars_today = int(today_m.group(1).replace(",", ""))

        results.append({
            "full_name": name,
            "html_url": f"https://github.com/{name}",
            "description": desc,
            "language": lang,
            "stargazers_count": total_stars,
            "stars_today": stars_today,
        })

    return results


class GitHubTrendingTool:
    name = "github_trending"
    description = (
        "获取 GitHub Trending 页面上的热门仓库。"
        "可按语言过滤，支持 daily/weekly/monthly。"
    )
    schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "最多返回的仓库数",
            },
            "language": {
                "type": "string",
                "default": "",
                "description": "按语言过滤，留空表示全部",
            },
            "since": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly"],
                "default": "daily",
                "description": "时间范围",
            },
        },
    }
    scopes: list[str] = []
    is_builtin = False
    cacheable = True
    cache_ttl = 600

    async def execute(self, ctx, args):
        limit = args.get("limit", 10)
        language = args.get("language", "")
        since = args.get("since", "daily")

        url = f"{TRENDING_URL}?since={since}"
        if language:
            url += f"&language={language}"

        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                    },
                    follow_redirects=True,
                )
                resp.raise_for_status()

            entries = _parse_trending(resp.text)[:limit]

            lines = [f"GitHub Trending ({since}): {len(entries)} 个仓库\n"]
            for e in entries:
                lines.append(
                    f"- [{e['full_name']}]({e['html_url']}) | {e['language']} | stars:{e['stargazers_count']} | today:{e['stars_today']} | {e['description'][:150]}"
                )
            content = "\n".join(lines)

            # Best-effort 保存 artifact
            svc = ctx.services.artifact
            if svc and entries:
                try:
                    text = json.dumps(entries, ensure_ascii=False, indent=2)
                    await svc.save(
                        user_id=ctx.user_id,
                        title="gh-trending-raw",
                        content=text,
                        mime_type="application/json",
                        source_tool_name="github_trending",
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


TOOL = GitHubTrendingTool()
