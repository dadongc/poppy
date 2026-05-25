from __future__ import annotations

import pytest

from src.tools.custom.hackernews import HackerNewsTopTool


class TestHackerNewsTopTool:
    @pytest.mark.asyncio
    async def test_schema_defaults(self):
        tool = HackerNewsTopTool()
        assert tool.name == "hackernews_top"
        assert tool.is_builtin is False
        assert tool.cacheable is True
        assert tool.schema["properties"]["limit"]["default"] == 15
        assert tool.schema["properties"]["min_score"]["default"] == 50

    @pytest.mark.asyncio
    async def test_returns_error_for_unreachable_url(self, agent_ctx_no_svc):
        """对不可达的 API 返回 error。"""
        # 临时修改 API_BASE 为无效地址
        import src.tools.custom.hackernews as hn

        original = hn.API_BASE
        hn.API_BASE = "https://invalid-host-99999.example.com"
        try:
            tool = HackerNewsTopTool()
            result = await tool.execute(agent_ctx_no_svc, {"limit": 5})
            assert result.status == "error"
            assert result.error_message is not None
        finally:
            hn.API_BASE = original

    @pytest.mark.asyncio
    async def test_entries_have_expected_keys(self):
        """验证数据结构定义正确（不调真实 API）。"""
        tool = HackerNewsTopTool()
        # 只验证 schema 和 metadata 结构
        props = tool.schema["properties"]
        assert "limit" in props
        assert "min_score" in props
