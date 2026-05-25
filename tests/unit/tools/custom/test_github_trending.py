from __future__ import annotations

import pytest

from src.tools.custom.github_trending import GitHubTrendingTool, _parse_trending

FAKE_HTML = """
<div>
<article class="Box-row" data-testid="1">
  <h2><a href="/owner1/repo1">owner1 / repo1</a></h2>
  <p class="col-9 color-fg-muted">A great repository description</p>
  <span itemprop="programmingLanguage">Python</span>
  <a>1,234</a>
  <span>567 stars today</span>
</article>
<article class="Box-row" data-testid="2">
  <h2><a href="/owner2/repo2">owner2 / repo2</a></h2>
  <p class="col-9 color-fg-muted">Another cool project</p>
  <span itemprop="programmingLanguage">Rust</span>
  <a>5,678</a>
  <span>890 stars today</span>
</article>
</div>
"""


class TestParseTrending:
    def test_parses_repo_names(self):
        entries = _parse_trending(FAKE_HTML)
        names = [e["full_name"] for e in entries]
        assert "owner1/repo1" in names
        assert "owner2/repo2" in names

    def test_parses_descriptions(self):
        entries = _parse_trending(FAKE_HTML)
        assert entries[0]["description"] == "A great repository description"

    def test_parses_language(self):
        entries = _parse_trending(FAKE_HTML)
        assert entries[0]["language"] == "Python"
        assert entries[1]["language"] == "Rust"

    def test_parses_stars_today(self):
        entries = _parse_trending(FAKE_HTML)
        assert entries[0]["stars_today"] == 567
        assert entries[1]["stars_today"] == 890

    def test_parses_total_stars(self):
        entries = _parse_trending(FAKE_HTML)
        assert entries[0]["stargazers_count"] == 1234
        assert entries[1]["stargazers_count"] == 5678

    def test_filters_sponsor_cards(self):
        html = """<article class="Box-row"><h2><a href="/sponsors/foo">foo</a></h2></article>"""
        assert _parse_trending(html) == []

    def test_empty_html(self):
        assert _parse_trending("") == []


class TestGitHubTrendingTool:
    @pytest.mark.asyncio
    async def test_schema_defaults(self):
        tool = GitHubTrendingTool()
        assert tool.name == "github_trending"
        assert tool.is_builtin is False
        assert tool.cacheable is True
        assert tool.schema["properties"]["limit"]["default"] == 10
        assert tool.schema["properties"]["since"]["default"] == "daily"

    @pytest.mark.asyncio
    async def test_returns_error_for_unreachable_url(self, agent_ctx_no_svc):
        import src.tools.custom.github_trending as gt

        original = gt.TRENDING_URL
        gt.TRENDING_URL = "https://invalid-host-99999.example.com/trending"
        try:
            tool = GitHubTrendingTool()
            result = await tool.execute(agent_ctx_no_svc, {"limit": 5})
            assert result.status == "error"
            assert result.error_message is not None
        finally:
            gt.TRENDING_URL = original

    @pytest.mark.asyncio
    async def test_language_filter_in_schema(self):
        tool = GitHubTrendingTool()
        props = tool.schema["properties"]
        assert "language" in props
        assert props["language"]["default"] == ""
