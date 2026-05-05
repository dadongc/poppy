from __future__ import annotations

import asyncio

import pytest

from src.common.types import (
    AgentContext,
    AgentSpec,
    Artifact,
    Event,
    ExecutionReport,
    KBChunk,
    KBDocument,
    KeywordHit,
    LLMChunk,
    LLMError,
    MemoryRecord,
    Message,
    PromptPayload,
    RetrievalHit,
    RetrievalQuery,
    RunInfo,
    Services,
    SessionInfo,
    SessionMessage,
    ToolCall,
    ToolResult,
    Usage,
    VectorHit,
    VectorItem,
)

# ---------------------------------------------------------------------------
# Message / ToolCall
# ---------------------------------------------------------------------------


class TestToolCall:
    def test_defaults(self):
        tc = ToolCall(call_id="c1", name="search")
        assert tc.call_id == "c1"
        assert tc.name == "search"
        assert tc.arguments == {}
        assert tc.arguments_raw == ""

    def test_with_arguments(self):
        tc = ToolCall(
            call_id="c1", name="search", arguments={"q": "hello"}, arguments_raw='{"q": "hello"}'
        )
        assert tc.arguments["q"] == "hello"
        assert tc.arguments_raw == '{"q": "hello"}'


class TestMessage:
    def test_minimal_user(self):
        m = Message(role="user", content="hello")
        assert m.role == "user"
        assert m.content == "hello"
        assert m.tool_calls == []
        assert m.tool_call_id == ""

    def test_assistant_with_tool_calls(self):
        m = Message(
            role="assistant",
            tool_calls=[ToolCall(call_id="c1", name="search")],
        )
        assert len(m.tool_calls) == 1
        assert m.tool_calls[0].name == "search"

    def test_tool_message(self):
        m = Message(role="tool", content="result", tool_call_id="c1", name="search")
        assert m.tool_call_id == "c1"
        assert m.name == "search"


# ---------------------------------------------------------------------------
# AgentSpec
# ---------------------------------------------------------------------------


class TestAgentSpec:
    def test_minimal(self):
        spec = AgentSpec(name="test-agent")
        assert spec.name == "test-agent"
        assert spec.max_steps == 20
        assert spec.token_budget == 50000
        assert spec.allowed_tools == set()

    def test_custom_budget(self):
        spec = AgentSpec(name="fast", max_steps=5, token_budget=8000)
        assert spec.max_steps == 5
        assert spec.token_budget == 8000


# ---------------------------------------------------------------------------
# AgentContext
# ---------------------------------------------------------------------------


class TestAgentContext:
    def test_minimal(self):
        ctx = AgentContext(run_id="run_01")
        assert ctx.run_id == "run_01"
        assert ctx.parent_run_id is None
        assert ctx.used_tokens == 0
        assert ctx.used_steps == 0
        assert isinstance(ctx.cancel_event, asyncio.Event)

    def test_with_spec(self):
        spec = AgentSpec(name="t")
        ctx = AgentContext(run_id="run_01", spec=spec)
        assert ctx.spec is not None
        assert ctx.spec.name == "t"

    def test_services_default(self):
        ctx = AgentContext(run_id="run_01")
        assert ctx.services is not None


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


class TestServices:
    def test_all_none_by_default(self):
        svc = Services()
        assert svc.session is None
        assert svc.memory is None
        assert svc.llm is None


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


class TestEvent:
    def test_minimal(self):
        e = Event(event_id="evt_01", type="run.started", run_id="run_01", ts=1.0)
        assert e.event_id == "evt_01"
        assert e.type == "run.started"
        assert e.level == "info"
        assert e.scope == "public"
        assert e.payload == {}

    def test_internal_event(self):
        e = Event(
            event_id="evt_01",
            type="llm.usage",
            run_id="run_01",
            ts=1.0,
            scope="internal",
            level="debug",
        )
        assert e.scope == "internal"
        assert e.level == "debug"


# ---------------------------------------------------------------------------
# ToolResult / ExecutionReport
# ---------------------------------------------------------------------------


class TestToolResult:
    def test_ok(self):
        tr = ToolResult(call_id="c1", name="search", status="ok", content="found")
        assert tr.status == "ok"
        assert tr.content == "found"

    def test_error(self):
        tr = ToolResult(
            call_id="c1",
            name="search",
            status="error",
            error_type="network",
            error_message="timeout",
        )
        assert tr.status == "error"
        assert tr.error_type == "network"


class TestExecutionReport:
    def test_empty(self):
        r = ExecutionReport()
        assert r.results == []
        assert r.total_duration_ms == 0

    def test_with_results(self):
        r = ExecutionReport(
            results=[ToolResult(call_id="c1", name="s", status="ok")],
            total_duration_ms=100,
            parallel_count=1,
            failed_count=0,
        )
        assert len(r.results) == 1
        assert r.failed_count == 0


# ---------------------------------------------------------------------------
# LLMChunk / Usage / LLMError
# ---------------------------------------------------------------------------


class TestLLMChunk:
    def test_text_delta(self):
        c = LLMChunk(type="text_delta", text="hello")
        assert c.type == "text_delta"
        assert c.text == "hello"

    def test_tool_call_start(self):
        c = LLMChunk(
            type="tool_call_start", tool_name="search", tool_call_id="c1", tool_call_index=0
        )
        assert c.tool_name == "search"

    def test_error(self):
        c = LLMChunk(
            type="error",
            error=LLMError(type="rate_limit", message="too many", retryable=True),
        )
        assert c.error is not None
        assert c.error.retryable is True

    def test_usage(self):
        c = LLMChunk(
            type="usage", usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        )
        assert c.usage is not None
        assert c.usage.total_tokens == 15


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------


class TestArtifact:
    def test_minimal(self):
        a = Artifact(artifact_id="atf_01")
        assert a.artifact_id == "atf_01"
        assert a.state == "active"
        assert a.mime_type == ""

    def test_with_source(self):
        a = Artifact(
            artifact_id="atf_01",
            source_type="tool_output",
            source_run_id="run_01",
            content_hash="abc123",
            size_bytes=1024,
        )
        assert a.source_type == "tool_output"
        assert a.size_bytes == 1024


# ---------------------------------------------------------------------------
# SessionMessage / SessionInfo
# ---------------------------------------------------------------------------


class TestSessionMessage:
    def test_minimal(self):
        sm = SessionMessage(msg_id="msg_01", session_id="ses_01")
        assert sm.msg_id == "msg_01"
        assert sm.session_id == "ses_01"
        assert sm.role == "user"
        assert sm.seq == 0


class TestSessionInfo:
    def test_minimal(self):
        si = SessionInfo(session_id="ses_01")
        assert si.session_id == "ses_01"
        assert si.title == ""
        assert si.message_count == 0


# ---------------------------------------------------------------------------
# MemoryRecord
# ---------------------------------------------------------------------------


class TestMemoryRecord:
    def test_minimal(self):
        m = MemoryRecord(memory_id="mem_01")
        assert m.memory_id == "mem_01"
        assert m.kind == "fact"
        assert m.confidence == 1.0
        assert m.state == "active"

    def test_custom_kind(self):
        m = MemoryRecord(memory_id="mem_01", kind="preference", importance=0.9)
        assert m.kind == "preference"
        assert m.importance == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# KBDocument / KBChunk
# ---------------------------------------------------------------------------


class TestKBDocument:
    def test_minimal(self):
        doc = KBDocument(doc_id="doc_01")
        assert doc.doc_id == "doc_01"
        assert doc.state == "ingesting"

    def test_ready(self):
        doc = KBDocument(doc_id="doc_01", state="ready", chunk_count=10)
        assert doc.state == "ready"
        assert doc.chunk_count == 10


class TestKBChunk:
    def test_minimal(self):
        ck = KBChunk(chunk_id="ck_01", doc_id="doc_01", seq=0)
        assert ck.chunk_id == "ck_01"
        assert ck.doc_id == "doc_01"
        assert ck.seq == 0
        assert ck.heading_path == []


# ---------------------------------------------------------------------------
# RunInfo
# ---------------------------------------------------------------------------


class TestRunInfo:
    def test_minimal(self):
        ri = RunInfo(run_id="run_01")
        assert ri.run_id == "run_01"
        assert ri.state == "pending"

    def test_completed(self):
        ri = RunInfo(run_id="run_01", state="completed", finished_at=1.0)
        assert ri.state == "completed"
        assert ri.finished_at == 1.0


# ---------------------------------------------------------------------------
# PromptPayload
# ---------------------------------------------------------------------------


class TestPromptPayload:
    def test_defaults(self):
        pp = PromptPayload()
        assert pp.messages == []
        assert pp.tools == []
        assert pp.temperature == 0.7

    def test_with_messages(self):
        pp = PromptPayload(messages=[{"role": "user", "content": "hi"}], token_estimate=10)
        assert len(pp.messages) == 1
        assert pp.token_estimate == 10


# ---------------------------------------------------------------------------
# Retrieval types
# ---------------------------------------------------------------------------


class TestRetrievalQuery:
    def test_minimal(self):
        q = RetrievalQuery(text="search", user_id="u1")
        assert q.text == "search"
        assert q.top_k == 8
        assert q.channels == ["kb"]

    def test_memory_channel(self):
        q = RetrievalQuery(text="search", user_id="u1", channels=["memory"])
        assert q.channels == ["memory"]


class TestRetrievalHit:
    def test_minimal(self):
        h = RetrievalHit(channel="kb")
        assert h.channel == "kb"
        assert h.score == 0.0

    def test_with_text(self):
        h = RetrievalHit(channel="memory", text="found", score=0.95, citation={"title": "T"})
        assert h.text == "found"
        assert h.score == pytest.approx(0.95)
        assert h.citation["title"] == "T"


class TestVectorItem:
    def test_minimal(self):
        vi = VectorItem(id="v1")
        assert vi.id == "v1"
        assert vi.vector == []

    def test_with_embedding(self):
        vi = VectorItem(id="v1", vector=[0.1, 0.2], metadata={"src": "kb"})
        assert len(vi.vector) == 2
        assert vi.metadata["src"] == "kb"


class TestVectorHit:
    def test_minimal(self):
        vh = VectorHit(id="v1", score=0.9)
        assert vh.id == "v1"
        assert vh.score == pytest.approx(0.9)


class TestKeywordHit:
    def test_minimal(self):
        kh = KeywordHit(id="k1")
        assert kh.id == "k1"
        assert kh.snippet == ""

    def test_with_snippet(self):
        kh = KeywordHit(id="k1", score=0.5, snippet="...found...")
        assert kh.snippet == "...found..."
