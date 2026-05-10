from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.common.clock import now_ts
from src.common.types import Artifact, MemoryRecord, RunInfo
from src.gateway.schemas import (
    AgentItem,
    CreateSessionIn,
    IngestDocIn,
    ListSessionsOut,
    RecallIn,
    RememberIn,
    SessionItem,
    StartRunIn,
    StartRunOut,
    artifact_to_out,
    memory_to_out,
    run_info_to_out,
)


class TestSessionSchemas:
    def test_create_session_in_defaults(self):
        s = CreateSessionIn()
        assert s.title == ""

    def test_create_session_in_with_title(self):
        s = CreateSessionIn(title="My Session")
        assert s.title == "My Session"

    def test_list_sessions_out(self):
        items = [
            SessionItem(
                session_id="ses_1",
                title="S1",
                created_at=1.0,
                last_active_at=2.0,
                message_count=5,
            )
        ]
        out = ListSessionsOut(items=items, next_cursor="2.0")
        assert out.next_cursor == "2.0"
        assert len(out.items) == 1


class TestRunSchemas:
    def test_start_run_in_required_message(self):
        with pytest.raises(ValidationError):
            StartRunIn()  # type: ignore[call-arg]

    def test_start_run_in_defaults(self):
        s = StartRunIn(message="hello")
        assert s.agent_name == "default"
        assert s.session_id == ""
        assert s.artifact_refs == []

    def test_start_run_out(self):
        out = StartRunOut(run_id="run_123")
        assert out.run_id == "run_123"

    def test_run_info_to_out(self):
        info = RunInfo(
            run_id="run_1",
            parent_run_id="parent_1",
            session_id="ses_1",
            user_id="u1",
            agent_name="test",
            state="running",
            started_at=1.0,
            finished_at=None,
            error="",
            used_tokens=100,
            used_steps=3,
        )
        d = run_info_to_out(info)
        assert "metadata" not in d
        assert d["run_id"] == "run_1"
        assert d["state"] == "running"


class TestArtifactSchemas:
    def test_artifact_to_out(self):
        a = Artifact(
            artifact_id="art_1",
            title="test.txt",
            mime_type="text/plain",
            size_bytes=100,
            summary="a file",
            preview="hello...",
            source_type="user_upload",
            created_at=now_ts(),
            state="active",
        )
        d = artifact_to_out(a)
        assert d["artifact_id"] == "art_1"
        assert d["size_bytes"] == 100


class TestMemorySchemas:
    def test_remember_in_defaults(self):
        r = RememberIn(content="remember this")
        assert r.kind == "fact"
        assert r.importance == 0.5
        assert r.confidence == 1.0
        assert r.tags == []

    def test_recall_in(self):
        r = RecallIn(query="my query", top_k=5, kinds=["fact", "preference"])
        assert r.top_k == 5
        assert len(r.kinds) == 2

    def test_memory_to_out(self):
        m = MemoryRecord(
            memory_id="mem_1",
            kind="fact",
            content="some fact",
            source_type="explicit",
            created_at=1.0,
            updated_at=2.0,
            importance=0.8,
            confidence=0.9,
            recall_count=3,
            state="active",
            tags=["tag1"],
        )
        d = memory_to_out(m)
        assert d["memory_id"] == "mem_1"
        assert d["kind"] == "fact"
        assert d["tags"] == ["tag1"]


class TestKBSchemas:
    def test_ingest_doc_in(self):
        d = IngestDocIn(artifact_id="art_1", title="doc title")
        assert d.source_type == "upload"
        assert d.source_uri == ""


class TestAgentSchemas:
    def test_agent_item(self):
        a = AgentItem(name="default", description="default agent", source="registry")
        assert a.name == "default"
        assert a.source == "registry"
