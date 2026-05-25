from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.gateway.deps import get_runtime
from src.runtime.runtime import Runtime

router = APIRouter()

_STYLE = """<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; background: #1a1a2e; color: #e0e0e0; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.7; }
  h1 { color: #7c3aed; border-bottom: 2px solid #7c3aed; padding-bottom: 10px; font-size: 1.8em; }
  h2 { color: #a78bfa; margin-top: 30px; font-size: 1.3em; }
  h3 { color: #c4b5fd; font-size: 1.1em; }
  a { color: #60a5fa; text-decoration: none; }
  a:hover { text-decoration: underline; }
  ul { padding-left: 20px; }
  li { margin: 4px 0; }
  strong { color: #fbbf24; }
  em { color: #94a3b8; }
  hr { border-color: #333; margin: 20px 0; }
  pre { background: #0f0f23; padding: 15px; border-radius: 8px; overflow-x: auto; border: 1px solid #333; }
  code { background: #0f0f23; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; color: #f472b6; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0; }
  th, td { border: 1px solid #333; padding: 8px 12px; text-align: left; }
  th { background: #0f0f23; color: #a78bfa; }
  tr:hover { background: rgba(124,58,237,0.1); }
  .back { display: inline-block; margin-bottom: 15px; color: #7c3aed; font-size: 0.9em; }
  .meta { color: #6b7280; font-size: 0.85em; margin-bottom: 20px; }
  .digest-list a { display: block; padding: 10px 15px; margin: 5px 0; background: #0f0f23; border-radius: 8px; border: 1px solid #222; transition: border-color .2s; }
  .digest-list a:hover { border-color: #7c3aed; text-decoration: none; }
  .digest-list span { color: #6b7280; font-size: 0.85em; }
  blockquote { border-left: 3px solid #7c3aed; padding-left: 15px; color: #94a3b8; margin: 10px 0; }
</style>"""


def _render_markdown(text: str) -> str:
    """简单 Markdown → HTML 转换。"""
    html = text
    # 转义
    html = html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 代码块
    html = re.sub(r"```(\w*)\n(.*?)```", r"<pre><code>\2</code></pre>", html, flags=re.DOTALL)
    # 行内代码
    html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)
    # 标题
    html = re.sub(r"^#### (.+)$", r"<h4>\1</h4>", html, flags=re.MULTILINE)
    html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
    # 粗体 + 斜体
    html = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", html)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)
    # 链接
    html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank">\1</a>', html)
    # 图片
    html = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img alt="\1" src="\2" style="max-width:100%">', html)
    # 水平线
    html = re.sub(r"^---$", r"<hr>", html, flags=re.MULTILINE)
    # 引用
    html = re.sub(r"^&gt; (.+)$", r"<blockquote>\1</blockquote>", html, flags=re.MULTILINE)
    # 表格
    html = re.sub(r"\|(.+)\|\n\|[- |]+\|\n((?:\|.+\|\n?)*)", _render_table, html)
    # 无序列表
    html = re.sub(r"(?:^[\-*] (.+)$\n?)+", _render_list, html, flags=re.MULTILINE)
    # 段落
    html = re.sub(r"\n\n+", r"</p><p>", html)
    html = f"<p>{html}</p>"
    # 清理空段落
    html = html.replace("<p></p>", "")
    html = html.replace("<p><h", "<h").replace("</h></p>", "</h>")
    html = html.replace("<p><pre>", "<pre>").replace("</pre></p>", "</pre>")
    html = html.replace("<p><ul>", "<ul>").replace("</ul></p>", "</ul>")
    html = html.replace("<p><blockquote>", "<blockquote>").replace("</blockquote></p>", "</blockquote>")
    html = html.replace("<p><table>", "<table>").replace("</table></p>", "</table>")
    return html


def _render_table(m: re.Match) -> str:
    header = m.group(1)
    rows = m.group(2).strip().split("\n")
    th_cells = "".join(f"<th>{c.strip()}</th>" for c in header.split("|") if c.strip())
    thead = f"<thead><tr>{th_cells}</tr></thead>"
    tbody_rows = []
    for row in rows:
        cells = "".join(f"<td>{c.strip()}</td>" for c in row.split("|") if c.strip())
        tbody_rows.append(f"<tr>{cells}</tr>")
    tbody = f"<tbody>{''.join(tbody_rows)}</tbody>"
    return f"<table>{thead}{tbody}</table>"


def _render_list(m: re.Match) -> str:
    items = re.findall(r"^[\-*] (.+)$", m.group(0), re.MULTILINE)
    lis = "".join(f"<li>{i}</li>" for i in items)
    return f"<ul>{lis}</ul>"


@router.get("/digest", response_class=HTMLResponse)
async def list_digests(runtime: Runtime = Depends(get_runtime)) -> HTMLResponse:
    """列出所有日报。"""
    svc = runtime.services.artifact
    if svc is None:
        raise HTTPException(500, "artifact service not available")

    rows = await svc.list_digests()

    items = ""
    for r in rows:
        title = r["title"].replace("daily-digest/", "")
        ts = r["created_at"]
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Asia/Shanghai")
        dt = datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d %H:%M BJT")
        items += f'<a href="/digest/{r["artifact_id"]}">{title} <span>{dt}</span></a>\n'

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Poppy Daily Digest</title>{_STYLE}</head>
<body>
<h1> 每日技术日报</h1>
<blockquote>Agent-driven · RSS + Hacker News + GitHub Trending</blockquote>
<div class="digest-list">{items if items else '<p>暂无日报</p>'}</div>
</body></html>"""
    return HTMLResponse(html)


@router.get("/digest/{artifact_id}", response_class=HTMLResponse)
async def view_digest(
    artifact_id: str,
    user_id: str = Query(default="cli-user"),
    runtime: Runtime = Depends(get_runtime),
) -> HTMLResponse:
    """查看单篇日报（Markdown 渲染为 HTML）。"""
    svc = runtime.services.artifact
    if svc is None:
        raise HTTPException(500, "artifact service not available")

    scheduler_uid = runtime._config.scheduler.user_id if runtime._config.scheduler else "scheduler"
    meta = None
    text = None
    for uid in {user_id, scheduler_uid}:
        try:
            meta = await svc.get_metadata(artifact_id, user_id=uid)
            text = await svc.get_text(artifact_id, user_id=uid)
            break
        except Exception:
            continue
    if meta is None or text is None:
        raise HTTPException(404, "artifact not found")

    body = _render_markdown(text)

    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Shanghai")
    dt = datetime.fromtimestamp(meta.created_at, tz=tz).strftime("%Y-%m-%d %H:%M BJT")

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{meta.title} — Poppy Digest</title>{_STYLE}</head>
<body>
<a class="back" href="/digest">← 返回日报列表</a>
<div class="meta">{dt} · {meta.size_bytes:,} bytes</div>
{body}
<hr><a class="back" href="/digest">← 返回日报列表</a>
</body></html>"""
    return HTMLResponse(html)
