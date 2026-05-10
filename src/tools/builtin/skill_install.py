from __future__ import annotations

import re

import httpx

from src.common.types import ToolResult

FETCH_TIMEOUT = 15.0
MAX_CONTENT_SIZE = 500_000  # 500KB


class SkillInstallTool:
    name = "skill_install"
    description = (
        "从 URL 安装外部 skill。skill 是一个 Markdown 文件，"
        "包含 YAML frontmatter 和说明文档。安装后可在 Agent 中使用。"
    )
    schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "skill 文件的 URL（.md 文件）",
            },
            "name": {
                "type": "string",
                "description": "可选：指定 skill 名称。默认从文件内容中解析。",
            },
        },
        "required": ["url"],
    }
    scopes: list[str] = []
    is_builtin = True
    cacheable = False
    cache_ttl = 0

    async def execute(self, ctx, args):
        url = args["url"]
        name_override = args.get("name", "").strip()

        if not url.startswith(("http://", "https://")):
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message="URL must start with http:// or https://",
            )

        skill_svc = ctx.services.skill
        if skill_svc is None:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message="skill registry not available",
            )

        # Fetch content
        try:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": "Poppy/1.0",
                        "Accept": "text/markdown,text/plain,text/html,*/*",
                    },
                    follow_redirects=True,
                )
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                text = resp.text

                if len(text) > MAX_CONTENT_SIZE:
                    return ToolResult(
                        call_id="",
                        name=self.name,
                        status="error",
                        error_message=f"content too large ({len(text)} bytes, max {MAX_CONTENT_SIZE})",
                    )

                # If HTML, try to extract raw markdown from GitHub pages etc.
                if "text/html" in content_type:
                    text = _extract_markdown_from_html(text, url)
                    if not text.strip():
                        return ToolResult(
                            call_id="",
                            name=self.name,
                            status="error",
                            error_message="could not extract markdown from HTML page",
                        )

        except httpx.HTTPStatusError as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=f"HTTP {e.response.status_code}",
            )
        except httpx.TimeoutException:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_type="Timeout",
                error_message=f"fetch timed out after {FETCH_TIMEOUT}s",
            )
        except Exception as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=str(e),
            )

        # Determine skill name
        name = name_override or _extract_name(text)
        if not name:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message="could not determine skill name from content, please provide 'name'",
            )

        # Validate name
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=f"invalid skill name: '{name}'. Use only letters, numbers, hyphens, and underscores.",
            )

        try:
            skill = await skill_svc.install(name, text)
            return ToolResult(
                call_id="",
                name=self.name,
                status="ok",
                content=f"已安装 skill '{skill.name}' (v{skill.version}) 到 {skill.source_path}",
                metadata={
                    "name": skill.name,
                    "version": skill.version,
                    "description": skill.description,
                    "source_kind": skill.source_kind,
                },
            )
        except Exception as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=str(e),
            )


def _extract_name(text: str) -> str:
    """Extract skill name from YAML frontmatter or first heading."""
    if text.startswith("---\n"):
        import yaml

        parts = text.split("---\n", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                name = meta.get("name", "")
                if name:
                    return str(name)
            except Exception:
                pass

    # Try first heading
    for line in text.split("\n"):
        if line.startswith("# "):
            name = line.lstrip("# ").strip()
            # Slugify
            name = re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_")
            return name.lower()

    return ""


def _extract_markdown_from_html(html: str, url: str) -> str:
    """Try to get raw markdown from GitHub-like pages."""
    # GitHub: raw URL pattern
    import re as _re

    # If it's a github.com blob URL, try to convert to raw
    gh_match = _re.match(
        r"https?://github\.com/([^/]+/[^/]+)/blob/(.+)", url
    )
    if gh_match:
        return (
            f"<!-- GitHub raw URL: "
            f"https://raw.githubusercontent.com/{gh_match.group(1)}/refs/heads/{gh_match.group(2)} -->\n"
        )

    # Basic extraction: look for markdown content in <article> or <pre>
    article = _re.search(
        r"<article[^>]*class=\"[^\"]*markdown[^\"]*\"[^>]*>(.*?)</article>",
        html,
        _re.DOTALL,
    )
    if article:
        from src.tools.builtin.web_fetch import _strip_html

        return _strip_html(article.group(1))

    return ""
