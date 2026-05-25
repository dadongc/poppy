from __future__ import annotations

import pytest

from src.tools.custom.rss_fetch import RssFetchTool, _parse_feed, _strip_markup, _struct_time_to_iso

FAKE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Blog</title>
    <link>https://example.com</link>
    <item>
      <title>Hello World</title>
      <link>https://example.com/hello</link>
      <author>Alice</author>
      <description>&lt;p&gt;This is a test post&lt;/p&gt;</description>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Second Post</title>
      <link>https://example.com/second</link>
      <description>Another test</description>
    </item>
  </channel>
</rss>"""


class TestStructTimeToIso:
    def test_converts_struct_time(self):
        import time as _time
        ts = _time.struct_time((2026, 5, 23, 8, 30, 0, 5, 143, 0))
        assert _struct_time_to_iso(ts) == "2026-05-23T08:30:00"

    def test_returns_empty_on_none(self):
        assert _struct_time_to_iso(None) == ""


class TestRssParseFeed:
    def test_parse_rss_extracts_entries(self):
        entries = _parse_feed(FAKE_RSS.encode("utf-8"))
        assert len(entries) == 2
        assert entries[0]["title"] == "Hello World"
        assert entries[0]["url"] == "https://example.com/hello"
        assert entries[0]["author"] == "Alice"
        assert entries[0]["published_at"] == "2024-01-01T00:00:00"

    def test_parse_rss_summary_strips_html(self):
        entries = _parse_feed(FAKE_RSS.encode("utf-8"))
        assert "This is a test post" in entries[0]["summary"]
        assert "<p>" not in entries[0]["summary"]

    def test_parse_empty_feed(self):
        entries = _parse_feed(b"")
        assert entries == []


class TestRssStripMarkup:
    def test_removes_html_tags(self):
        assert "Hello" == _strip_markup("<p>Hello</p>")

    def test_truncates_long_text(self):
        long_text = "x" * 500
        result = _strip_markup(long_text)
        assert len(result) <= 303  # max_len + "..."

    def test_handles_empty(self):
        assert _strip_markup("") == ""


class TestRssFetchTool:
    @pytest.mark.asyncio
    async def test_schema_has_required_fields(self):
        tool = RssFetchTool()
        assert "urls" in tool.schema.get("required", [])
        assert tool.is_builtin is False
        assert tool.cacheable is True
        assert tool.cache_ttl == 600

    @pytest.mark.asyncio
    async def test_invalid_url_returns_error_in_metadata(self, agent_ctx_no_svc):
        """无效 URL 不抛异常，错误记录在 metadata.errors 中。"""
        tool = RssFetchTool()
        result = await tool.execute(
            agent_ctx_no_svc,
            {"urls": ["not-a-valid-url"]},
        )
        assert result.status == "ok"  # 整体成功，部分源失败
        assert result.metadata["failed_count"] >= 1
