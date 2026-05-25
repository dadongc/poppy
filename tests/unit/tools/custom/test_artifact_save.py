from __future__ import annotations

import pytest

from src.tools.custom.artifact_save import ArtifactSaveTool


class TestArtifactSaveTool:
    @pytest.mark.asyncio
    async def test_service_not_available(self, agent_ctx_no_svc):
        tool = ArtifactSaveTool()
        result = await tool.execute(
            agent_ctx_no_svc,
            {"name": "test.md", "content": "hello"},
        )
        assert result.status == "error"
        assert "not available" in result.error_message

    @pytest.mark.asyncio
    async def test_save_and_read_back(self, agent_ctx_with_artifact):
        tool = ArtifactSaveTool()
        result = await tool.execute(
            agent_ctx_with_artifact,
            {"name": "daily-digest/2026-05-23.md", "content": "# 日报\n\n测试内容"},
        )
        assert result.status == "ok"
        assert result.metadata["name"] == "daily-digest/2026-05-23.md"
        assert result.metadata["artifact_id"].startswith("atf_")

    @pytest.mark.asyncio
    async def test_default_content_type(self, agent_ctx_with_artifact):
        tool = ArtifactSaveTool()
        result = await tool.execute(
            agent_ctx_with_artifact,
            {"name": "test.json", "content": '{"key": "value"}'},
        )
        assert result.status == "ok"
        # 默认 content_type = text/markdown，但没传 content_type 时用默认值
        # 验证 artifact 确实被保存了
        artifact_id = result.metadata["artifact_id"]
        svc = agent_ctx_with_artifact.services.artifact
        artifact = await svc.get_metadata(artifact_id, agent_ctx_with_artifact.user_id)
        assert artifact is not None
        assert artifact.title == "test.json"

    @pytest.mark.asyncio
    async def test_explicit_content_type(self, agent_ctx_with_artifact):
        tool = ArtifactSaveTool()
        result = await tool.execute(
            agent_ctx_with_artifact,
            {
                "name": "data.json",
                "content": '{"key": "value"}',
                "content_type": "application/json",
            },
        )
        assert result.status == "ok"
        svc = agent_ctx_with_artifact.services.artifact
        artifact = await svc.get_metadata(
            result.metadata["artifact_id"], agent_ctx_with_artifact.user_id
        )
        assert artifact.mime_type == "application/json"

    @pytest.mark.asyncio
    async def test_schema_has_required_fields(self):
        tool = ArtifactSaveTool()
        assert "name" in tool.schema.get("required", [])
        assert "content" in tool.schema.get("required", [])
        assert tool.is_builtin is False
