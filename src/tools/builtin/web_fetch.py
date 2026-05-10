from __future__ import annotations

import re

import httpx

from src.common.types import ToolResult

MAX_CONTENT_CHARS = 8000
FETCH_TIMEOUT = 15.0


def _strip_html(text: str) -> str:
    """Basic HTML to plain text converter."""
    # Remove scripts and styles
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Replace block elements with newlines
    text = re.sub(r"</?(?:p|div|br|h[1-6]|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Remove remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#x27;", "'").replace("&nbsp;", " ")
    # Collapse whitespace
    text = re.sub(r"\n\s*\n", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


class WebFetchTool:
    name = "web_fetch"
    description = (
        "获取指定 URL 的网页内容并提取正文文本。"
        "适用于需要阅读某个网页具体内容的场景。"
    )
    schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要抓取的网页 URL",
            },
            "max_chars": {
                "type": "integer",
                "default": 5000,
                "description": "返回内容最大字符数，超出截断",
            },
        },
        "required": ["url"],
    }
    scopes: list[str] = []
    is_builtin = True
    cacheable = True
    cache_ttl = 300

    async def execute(self, ctx, args):
        url = args["url"]
        max_chars = min(args.get("max_chars", 5000), MAX_CONTENT_CHARS)

        if not url.startswith(("http://", "https://")):
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message="URL must start with http:// or https://",
            )

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
                        "Accept": "text/html,application/xhtml+xml,*/*",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    },
                    follow_redirects=True,
                )
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                if "text/html" not in content_type and "text/plain" not in content_type:
                    return ToolResult(
                        call_id="",
                        name=self.name,
                        status="error",
                        error_message=f"unsupported content type: {content_type}",
                    )

                text = resp.text
                if "text/html" in content_type:
                    text = _strip_html(text)

                truncated = len(text) > max_chars
                text = text[:max_chars] + ("\n...[truncated]" if truncated else "")

                return ToolResult(
                    call_id="",
                    name=self.name,
                    status="ok",
                    content=text,
                    metadata={
                        "url": str(resp.url),
                        "status_code": resp.status_code,
                        "content_type": content_type,
                    },
                )
        except httpx.HTTPStatusError as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=f"HTTP {e.response.status_code}: {e.response.reason_phrase}",
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
