from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.common.clock import now_ts
from src.common.ids import msg_id, new_id
from src.common.types import (
    Event,
    EventType,
    Message,
    SessionInfo,
    SessionMessage,
    ToolCall,
)
from src.infra.protocols import Cache, EventBus, JobQueue, RelationalStore
from src.service.llm_protocol import LLMService


@dataclass(slots=True)
class SessionWindow:
    summary: str
    summary_covers_until_seq: int
    messages: list[SessionMessage]


def _serialize_tool_calls(tool_calls: list[ToolCall]) -> str:
    return json.dumps(
        [
            {
                "call_id": tc.call_id,
                "name": tc.name,
                "arguments": tc.arguments,
                "arguments_raw": tc.arguments_raw,
            }
            for tc in tool_calls
        ],
        ensure_ascii=False,
    )


def _deserialize_tool_calls(raw: object) -> list[ToolCall]:
    if raw is None:
        return []
    if isinstance(raw, str):
        data = json.loads(raw)
    elif isinstance(raw, list):
        data = raw
    else:
        return []
    return [
        ToolCall(
            call_id=d["call_id"],
            name=d["name"],
            arguments=d.get("arguments", {}),
            arguments_raw=d.get("arguments_raw", ""),
        )
        for d in data
    ]


def _json_list(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, list):
        return raw
    return []


class SessionService:
    SUMMARY_TRIGGER_MSGS = 30
    KEEP_RECENT_AFTER_SUMMARY = 10

    def __init__(
        self,
        *,
        store: RelationalStore,
        cache: Cache,
        event_bus: EventBus,
        jobs: JobQueue | None,
        llm: LLMService,
    ) -> None:
        self._store = store
        self._cache = cache
        self._event_bus = event_bus
        self._jobs = jobs
        self._llm = llm

    # ------------------------------------------------------------------
    # create / get / list
    # ------------------------------------------------------------------

    async def create(self, user_id: str, title: str = "") -> SessionInfo:
        sid = new_id("ses")
        ts = now_ts()
        await self._store.execute(
            """INSERT INTO sessions(session_id, user_id, title, created_at, last_active_at)
               VALUES (?, ?, ?, ?, ?)""",
            sid,
            user_id,
            title,
            ts,
            ts,
        )
        return SessionInfo(
            session_id=sid,
            user_id=user_id,
            title=title,
            created_at=ts,
            last_active_at=ts,
        )

    async def get(self, session_id: str, user_id: str) -> SessionInfo | None:
        cached = await self._cache.get(f"ses:{session_id}")
        if cached is not None:
            return SessionInfo(**cached) if isinstance(cached, dict) else None

        row = await self._store.fetch_one(
            "SELECT * FROM sessions WHERE session_id = ? AND user_id = ?",
            session_id,
            user_id,
        )
        if row is None:
            return None

        info = self._row_to_info(row)
        await self._cache.set(f"ses:{session_id}", self._info_to_dict(info), ttl=300)
        return info

    async def list_by_user(
        self, user_id: str, limit: int = 50, cursor: str | None = None
    ) -> list[SessionInfo]:
        if cursor:
            rows = await self._store.fetch_all(
                """SELECT * FROM sessions
                   WHERE user_id = ? AND last_active_at < ?
                   ORDER BY last_active_at DESC LIMIT ?""",
                user_id,
                cursor,
                limit,
            )
        else:
            rows = await self._store.fetch_all(
                "SELECT * FROM sessions WHERE user_id = ? ORDER BY last_active_at DESC LIMIT ?",
                user_id,
                limit,
            )
        return [self._row_to_info(r) for r in rows]

    # ------------------------------------------------------------------
    # append
    # ------------------------------------------------------------------

    async def append_message(
        self,
        session_id: str,
        user_id: str,
        msg: Message,
        run_id: str | None = None,
    ) -> SessionMessage:
        row = await self._store.fetch_one(
            "SELECT COALESCE(MAX(seq), 0) as max_seq FROM session_messages WHERE session_id = ?",
            session_id,
        )
        next_seq = (row["max_seq"] if row else 0) + 1

        mid = msg.msg_id or msg_id()
        ts = msg.created_at or now_ts()
        await self._store.execute(
            """INSERT INTO session_messages(msg_id, session_id, user_id, seq, run_id, role,
               content, tool_calls, tool_call_id, name, artifact_refs, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            mid,
            session_id,
            user_id,
            next_seq,
            run_id or "",
            msg.role,
            msg.content,
            _serialize_tool_calls(msg.tool_calls),
            msg.tool_call_id,
            msg.name,
            json.dumps(getattr(msg, "artifact_refs", []), ensure_ascii=False),
            ts,
        )
        await self._store.execute(
            """UPDATE sessions SET message_count = message_count + 1, last_active_at = ?
               WHERE session_id = ?""",
            ts,
            session_id,
        )
        await self._cache.delete(f"ses:{session_id}")
        await self._event_bus.publish(
            Event(
                event_id=new_id("evt"),
                type=EventType.SESSION_MESSAGE_ADDED,
                run_id=run_id or "",
                user_id=user_id,
                ts=ts,
                payload={"msg_id": mid, "seq": next_seq},
            )
        )
        return SessionMessage(
            msg_id=mid,
            session_id=session_id,
            user_id=user_id,
            seq=next_seq,
            run_id=run_id,
            role=msg.role,
            content=msg.content,
            tool_calls=msg.tool_calls,
            tool_call_id=msg.tool_call_id,
            name=msg.name,
            artifact_refs=[],
            created_at=ts,
        )

    async def append_messages(
        self,
        session_id: str,
        user_id: str,
        msgs: list[Message],
        run_id: str,
    ) -> list[SessionMessage]:
        async with self._store.transaction() as tx:
            row = await tx.fetch_one(
                "SELECT COALESCE(MAX(seq), 0) as max_seq FROM session_messages WHERE session_id = ?",
                session_id,
            )
            seq = (row["max_seq"] if row else 0) + 1
            result: list[SessionMessage] = []
            ts = now_ts()

            for msg in msgs:
                mid = msg.msg_id or msg_id()
                await tx.execute(
                    """INSERT INTO session_messages(msg_id, session_id, user_id, seq, run_id, role,
                       content, tool_calls, tool_call_id, name, artifact_refs, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    mid,
                    session_id,
                    user_id,
                    seq,
                    run_id,
                    msg.role,
                    msg.content,
                    _serialize_tool_calls(msg.tool_calls),
                    msg.tool_call_id,
                    msg.name,
                    json.dumps(getattr(msg, "artifact_refs", []), ensure_ascii=False),
                    ts,
                )
                result.append(
                    SessionMessage(
                        msg_id=mid,
                        session_id=session_id,
                        user_id=user_id,
                        seq=seq,
                        run_id=run_id,
                        role=msg.role,
                        content=msg.content,
                        tool_calls=msg.tool_calls,
                        tool_call_id=msg.tool_call_id,
                        name=msg.name,
                        artifact_refs=[],
                        created_at=ts,
                    )
                )
                seq += 1

            await tx.execute(
                """UPDATE sessions SET message_count = message_count + ?, last_active_at = ?
                   WHERE session_id = ?""",
                len(msgs),
                ts,
                session_id,
            )
        await self._cache.delete(f"ses:{session_id}")
        return result

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------

    async def get_recent(
        self,
        session_id: str,
        user_id: str,
        limit: int = 50,
        before_seq: int | None = None,
    ) -> list[SessionMessage]:
        if before_seq is not None:
            rows = await self._store.fetch_all(
                """SELECT * FROM session_messages
                   WHERE session_id = ? AND user_id = ? AND seq < ?
                   ORDER BY seq DESC LIMIT ?""",
                session_id,
                user_id,
                before_seq,
                limit,
            )
        else:
            rows = await self._store.fetch_all(
                """SELECT * FROM session_messages
                   WHERE session_id = ? AND user_id = ?
                   ORDER BY seq DESC LIMIT ?""",
                session_id,
                user_id,
                limit,
            )
        return [self._row_to_msg(r) for r in reversed(rows)]

    async def get_window_for_context(
        self, session_id: str, user_id: str, limit: int = 20
    ) -> SessionWindow:
        info = await self.get(session_id, user_id)
        if info is None:
            return SessionWindow("", 0, [])

        rows = await self._store.fetch_all(
            """SELECT * FROM session_messages
               WHERE session_id = ? AND user_id = ? AND seq > ?
               ORDER BY seq DESC LIMIT ?""",
            session_id,
            user_id,
            info.summary_covers_until_seq,
            limit,
        )
        msgs = [self._row_to_msg(r) for r in reversed(rows)]
        msgs = self._ensure_tool_pairs(msgs)
        return SessionWindow(info.summary, info.summary_covers_until_seq, msgs)

    # ------------------------------------------------------------------
    # summarize
    # ------------------------------------------------------------------

    async def maybe_summarize(self, session_id: str, user_id: str) -> bool:
        info = await self.get(session_id, user_id)
        if info is None:
            return False
        pending = info.message_count - info.summary_covers_until_seq
        if pending < self.SUMMARY_TRIGGER_MSGS:
            return False
        if self._jobs is not None:
            await self._jobs.enqueue(
                "session.compress",
                {"session_id": session_id, "user_id": user_id},
                max_retries=2,
            )
        else:
            await self._compress_session(session_id, user_id)
        return True

    async def _compress_session(self, session_id: str, user_id: str) -> None:
        info = await self.get(session_id, user_id)
        if info is None:
            return
        end_seq = info.message_count - self.KEEP_RECENT_AFTER_SUMMARY
        if end_seq <= info.summary_covers_until_seq:
            return

        rows = await self._store.fetch_all(
            """SELECT * FROM session_messages
               WHERE session_id = ? AND seq > ? AND seq <= ?
               ORDER BY seq ASC""",
            session_id,
            info.summary_covers_until_seq,
            end_seq,
        )
        new_text = "\n".join(
            f"[{r['role']}] {r['content']}" for r in rows
        )
        prompt = (
            f"已有摘要：\n{info.summary or '（暂无）'}\n\n"
            f"新增对话：\n{new_text}\n\n"
            f"融入并保留关键事实/决定/偏好，500 字内。"
        )
        new_summary = await self._llm.complete_simple(prompt, max_tokens=600)
        ts = now_ts()
        await self._store.execute(
            """UPDATE sessions SET summary = ?, summary_covers_until_seq = ?,
               last_active_at = ? WHERE session_id = ?""",
            new_summary,
            end_seq,
            ts,
            session_id,
        )
        await self._cache.delete(f"ses:{session_id}")
        await self._event_bus.publish(
            Event(
                event_id=new_id("evt"),
                type=EventType.SESSION_SUMMARIZED,
                run_id="",
                session_id=session_id,
                user_id=user_id,
                ts=ts,
                payload={"covers_until": end_seq},
            )
        )

    # ------------------------------------------------------------------
    # tool-pair safety
    # ------------------------------------------------------------------

    def _ensure_tool_pairs(self, msgs: list[SessionMessage]) -> list[SessionMessage]:
        tool_call_ids: set[str] = set()
        tool_msg_ids: set[str] = set()

        for m in msgs:
            for tc in m.tool_calls:
                tool_call_ids.add(tc.call_id)
            if m.role == "tool" and m.tool_call_id:
                tool_msg_ids.add(m.tool_call_id)

        result: list[SessionMessage] = []
        for m in msgs:
            if m.role == "tool":
                if m.tool_call_id not in tool_call_ids:
                    continue
            elif m.role == "assistant" and m.tool_calls:
                paired = all(tc.call_id in tool_msg_ids for tc in m.tool_calls)
                if not paired:
                    m = SessionMessage(
                        msg_id=m.msg_id,
                        session_id=m.session_id,
                        user_id=m.user_id,
                        seq=m.seq,
                        run_id=m.run_id,
                        role=m.role,
                        content=m.content,
                        tool_calls=[],
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                        artifact_refs=m.artifact_refs,
                        created_at=m.created_at,
                        metadata=m.metadata,
                    )
            result.append(m)
        return result

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _row_to_msg(self, row: dict[str, Any]) -> SessionMessage:
        return SessionMessage(
            msg_id=row["msg_id"],
            session_id=row["session_id"],
            user_id=row["user_id"],
            seq=row["seq"],
            run_id=row.get("run_id"),
            role=row["role"],
            content=row["content"],
            tool_calls=_deserialize_tool_calls(row.get("tool_calls")),
            tool_call_id=row.get("tool_call_id", ""),
            name=row.get("name", ""),
            artifact_refs=_json_list(row.get("artifact_refs")),
            created_at=row.get("created_at", 0.0),
            metadata=_json_dict(row.get("metadata")),
        )

    @staticmethod
    def _row_to_info(row: dict[str, Any]) -> SessionInfo:
        return SessionInfo(
            session_id=row["session_id"],
            user_id=row["user_id"],
            title=row.get("title", ""),
            created_at=row.get("created_at", 0.0),
            last_active_at=row.get("last_active_at", 0.0),
            message_count=row.get("message_count", 0),
            summary=row.get("summary", ""),
            summary_covers_until_seq=row.get("summary_covers_until_seq", 0),
            metadata=_json_dict(row.get("metadata")),
        )

    @staticmethod
    def _info_to_dict(info: SessionInfo) -> dict[str, Any]:
        return {
            "session_id": info.session_id,
            "user_id": info.user_id,
            "title": info.title,
            "created_at": info.created_at,
            "last_active_at": info.last_active_at,
            "message_count": info.message_count,
            "summary": info.summary,
            "summary_covers_until_seq": info.summary_covers_until_seq,
            "metadata": info.metadata,
        }


def _json_dict(raw: object) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    return {}
