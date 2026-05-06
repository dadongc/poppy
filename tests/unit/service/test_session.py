from __future__ import annotations

import pytest

from src.common.clock import now_ts
from src.common.ids import new_id
from src.common.types import EventType, Message, SessionMessage, ToolCall
from src.infra.eventbus.inproc import InProcessEventBus
from src.infra.relational.sqlite import SqliteStore
from src.service.llm_protocol import StubLLM
from src.service.session import SessionService


@pytest.fixture
async def svc(sqlite_store: SqliteStore, sessions_tables, mem_cache, stub_llm):
    bus = InProcessEventBus(store=None, persist=False)
    await bus.init()
    svc = SessionService(
        store=sqlite_store,
        cache=mem_cache,
        event_bus=bus,
        jobs=None,
        llm=stub_llm,
    )
    yield svc
    await bus.shutdown()


class TestSessionCreateAndGet:
    async def test_create_returns_session_info(self, svc):
        info = await svc.create("u1", title="hello")
        assert info.session_id.startswith("ses_")
        assert info.user_id == "u1"
        assert info.title == "hello"
        assert info.message_count == 0

    async def test_get_returns_info(self, svc):
        created = await svc.create("u1")
        fetched = await svc.get(created.session_id, "u1")
        assert fetched is not None
        assert fetched.session_id == created.session_id

    async def test_get_returns_none_for_missing(self, svc):
        result = await svc.get("ses_nonexistent", "u1")
        assert result is None

    async def test_list_by_user_returns_multiple(self, svc):
        await svc.create("u1", title="a")
        await svc.create("u1", title="b")
        await svc.create("u1", title="c")
        items = await svc.list_by_user("u1")
        assert len(items) >= 3

    async def test_list_by_user_pagination(self, svc):
        for i in range(5):
            await svc.create("u1", title=f"t{i}")
        items = await svc.list_by_user("u1", limit=2)
        assert len(items) == 2


class TestSessionAppend:
    async def test_append_assigns_monotonic_seq(self, svc):
        info = await svc.create("u1")
        sid = info.session_id
        m1 = await svc.append_message(sid, "u1", Message(role="user", content="hi"))
        m2 = await svc.append_message(sid, "u1", Message(role="assistant", content="hello"))
        m3 = await svc.append_message(sid, "u1", Message(role="user", content="bye"))
        assert m1.seq == 1
        assert m2.seq == 2
        assert m3.seq == 3

    async def test_append_increments_message_count(self, svc):
        info = await svc.create("u1")
        await svc.append_message(info.session_id, "u1", Message(role="user", content="hi"))
        updated = await svc.get(info.session_id, "u1")
        assert updated is not None
        assert updated.message_count == 1

    async def test_concurrent_sessions_independent_seqs(self, svc):
        s1 = await svc.create("u1")
        s2 = await svc.create("u1")
        await svc.append_message(s1.session_id, "u1", Message(role="user", content="a"))
        await svc.append_message(s2.session_id, "u1", Message(role="user", content="b"))
        await svc.append_message(s1.session_id, "u1", Message(role="user", content="c"))
        msgs1 = await svc.get_recent(s1.session_id, "u1")
        msgs2 = await svc.get_recent(s2.session_id, "u1")
        assert [m.seq for m in msgs1] == [1, 2]
        assert [m.seq for m in msgs2] == [1]

    async def test_append_messages_atomic(self, svc):
        info = await svc.create("u1")
        msgs = [
            Message(role="user", content="q"),
            Message(role="assistant", content="a"),
            Message(role="user", content="r"),
        ]
        result = await svc.append_messages(info.session_id, "u1", msgs, run_id="r1")
        assert len(result) == 3
        assert result[0].seq == 1
        assert result[2].seq == 3
        assert result[0].run_id == "r1"


class TestSessionWindow:
    async def test_get_recent_returns_messages(self, svc):
        info = await svc.create("u1")
        sid = info.session_id
        await svc.append_message(sid, "u1", Message(role="user", content="hi"))
        msgs = await svc.get_recent(sid, "u1")
        assert len(msgs) == 1
        assert msgs[0].content == "hi"

    async def test_window_returns_summary_and_messages(self, svc):
        info = await svc.create("u1")
        sid = info.session_id
        await svc.append_message(sid, "u1", Message(role="user", content="hello"))
        window = await svc.get_window_for_context(sid, "u1")
        assert window.summary == ""
        assert window.summary_covers_until_seq == 0
        assert len(window.messages) == 1

    async def test_ensure_tool_pairs_drops_orphan_tool(self, svc):
        info = await svc.create("u1")
        sid = info.session_id
        await svc.append_message(sid, "u1", Message(role="tool", content="result", tool_call_id="tc1"))
        window = await svc.get_window_for_context(sid, "u1")
        assert len(window.messages) == 0

    async def test_ensure_tool_pairs_preserves_valid_pairs(self, svc):
        info = await svc.create("u1")
        sid = info.session_id
        await svc.append_message(
            sid, "u1",
            Message(role="assistant", content="", tool_calls=[ToolCall(call_id="tc1", name="test")]),
        )
        await svc.append_message(sid, "u1", Message(role="tool", content="ok", tool_call_id="tc1"))
        window = await svc.get_window_for_context(sid, "u1")
        assert len(window.messages) == 2

    async def test_ensure_tool_pairs_clears_incomplete_tool_calls(self, svc):
        info = await svc.create("u1")
        sid = info.session_id
        await svc.append_message(
            sid, "u1",
            Message(role="assistant", content="", tool_calls=[ToolCall(call_id="tc1", name="test")]),
        )
        window = await svc.get_window_for_context(sid, "u1")
        assert len(window.messages) == 1
        assert window.messages[0].tool_calls == []


class TestSessionCompress:
    async def test_maybe_summarize_noop_below_threshold(self, svc):
        info = await svc.create("u1")
        sid = info.session_id
        for i in range(25):
            await svc.append_message(sid, "u1", Message(role="user", content=f"msg{i}"))
        result = await svc.maybe_summarize(sid, "u1")
        assert result is False

    async def test_maybe_summarize_triggers_when_threshold(self, svc):
        info = await svc.create("u1")
        sid = info.session_id
        for i in range(35):
            await svc.append_message(sid, "u1", Message(role="user", content=f"msg{i}"))
        result = await svc.maybe_summarize(sid, "u1")
        assert result is True
        updated = await svc.get(sid, "u1")
        assert updated is not None
        assert updated.summary != ""

    async def test_compress_updates_summary_and_covers_until(self, svc):
        info = await svc.create("u1")
        sid = info.session_id
        for i in range(45):
            await svc.append_message(sid, "u1", Message(role="user", content=f"msg{i}"))
        await svc.maybe_summarize(sid, "u1")
        updated = await svc.get(sid, "u1")
        assert updated is not None
        assert "stub summary" in updated.summary
        assert updated.summary_covers_until_seq == 35  # 45 - 10

    async def test_compress_keeps_recent_messages(self, svc):
        info = await svc.create("u1")
        sid = info.session_id
        for i in range(45):
            await svc.append_message(sid, "u1", Message(role="user", content=f"msg{i}"))
        await svc.maybe_summarize(sid, "u1")
        window = await svc.get_window_for_context(sid, "u1")
        assert len(window.messages) == 10  # KEEP_RECENT_AFTER_SUMMARY

    async def test_window_excludes_summarized(self, svc):
        info = await svc.create("u1")
        sid = info.session_id
        for i in range(45):
            await svc.append_message(sid, "u1", Message(role="user", content=f"msg{i}"))
        await svc.maybe_summarize(sid, "u1")
        # get_recent without before_seq should return after summary
        recent = await svc.get_recent(sid, "u1")
        assert all(r.seq > info.summary_covers_until_seq for r in recent)
