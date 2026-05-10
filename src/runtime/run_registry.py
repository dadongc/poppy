from __future__ import annotations

import asyncio
import json

from src.common.clock import now_ts
from src.common.types import RunInfo
from src.infra.protocols import RelationalStore


class RunRegistry:
    """Run 生命周期注册表 + 闭包表管理。"""

    def __init__(self, store: RelationalStore) -> None:
        self._store = store
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        await self._store.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                parent_run_id TEXT,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                started_at REAL NOT NULL DEFAULT 0,
                finished_at REAL,
                error TEXT DEFAULT '',
                used_tokens INTEGER NOT NULL DEFAULT 0,
                used_steps INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}'
            )
        """)
        await self._store.execute("""
            CREATE TABLE IF NOT EXISTS run_closure (
                ancestor TEXT NOT NULL,
                descendant TEXT NOT NULL,
                depth INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (ancestor, descendant)
            )
        """)

    async def register(
        self,
        run_id: str,
        agent_name: str,
        session_id: str = "",
        user_id: str = "",
        parent_run_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        async with self._store.transaction() as tx:
            await tx.execute(
                """INSERT INTO runs (run_id, parent_run_id, session_id, user_id,
                   agent_name, state, started_at, used_tokens, used_steps, metadata)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                run_id,
                parent_run_id or "",
                session_id,
                user_id,
                agent_name,
                "pending",
                now_ts(),
                0,
                0,
                json.dumps(metadata or {}, ensure_ascii=False),
            )
            await tx.execute(
                "INSERT INTO run_closure (ancestor, descendant, depth) VALUES ($1, $1, 0)",
                run_id,
            )
            if parent_run_id:
                await tx.execute(
                    """INSERT INTO run_closure (ancestor, descendant, depth)
                       SELECT ancestor, $1, depth + 1
                       FROM run_closure WHERE descendant = $2""",
                    run_id,
                    parent_run_id,
                )

    async def attach_cancel_event(self, run_id: str, ev: asyncio.Event) -> None:
        async with self._lock:
            self._cancel_events[run_id] = ev

    async def cancel(self, run_id: str) -> int:
        rows = await self._store.fetch_all(
            "SELECT descendant FROM run_closure WHERE ancestor = $1",
            run_id,
        )
        ids = [r["descendant"] for r in rows]
        if not ids:
            ids = [run_id]

        async with self._lock:
            for rid in ids:
                ev = self._cancel_events.get(rid)
                if ev and not ev.is_set():
                    ev.set()

        for rid in ids:
            await self._store.execute(
                """UPDATE runs SET state = 'cancelled', finished_at = $2
                   WHERE run_id = $1 AND state IN ('pending','running')""",
                rid,
                now_ts(),
            )
        return len(ids)

    async def update_state(self, run_id: str, state: str, **fields) -> None:
        sets = ["state = $2"]
        params: list = [run_id, state]
        idx = 3
        if state in ("completed", "failed", "cancelled", "timeout"):
            sets.append(f"finished_at = ${idx}")
            params.append(now_ts())
            idx += 1
        for k, v in fields.items():
            sets.append(f"{k} = ${idx}")
            params.append(v)
            idx += 1
        params.append(run_id)
        await self._store.execute(
            f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ${idx}",
            *params,
        )

        if state in ("completed", "failed", "cancelled", "timeout"):
            async with self._lock:
                self._cancel_events.pop(run_id, None)

    async def get(self, run_id: str) -> RunInfo | None:
        row = await self._store.fetch_one(
            "SELECT * FROM runs WHERE run_id = $1", run_id
        )
        return self._row_to_info(row) if row else None

    async def list_active(self) -> list[RunInfo]:
        rows = await self._store.fetch_all(
            "SELECT * FROM runs WHERE state IN ('pending','running')"
        )
        return [self._row_to_info(r) for r in rows]

    async def descendants(self, run_id: str) -> list[str]:
        rows = await self._store.fetch_all(
            "SELECT descendant FROM run_closure WHERE ancestor = $1 AND depth > 0",
            run_id,
        )
        return [r["descendant"] for r in rows]

    async def count_active_children(self, run_id: str) -> int:
        row = await self._store.fetch_one(
            """SELECT COUNT(*) as cnt FROM run_closure rc
               JOIN runs r ON rc.descendant = r.run_id
               WHERE rc.ancestor = $1 AND rc.depth = 1
               AND r.state IN ('pending','running')""",
            run_id,
        )
        return row["cnt"] if row else 0

    @staticmethod
    def _row_to_info(row: dict) -> RunInfo:
        return RunInfo(
            run_id=row["run_id"],
            parent_run_id=row.get("parent_run_id") or None,
            session_id=row.get("session_id", ""),
            user_id=row.get("user_id", ""),
            agent_name=row.get("agent_name", ""),
            state=row.get("state", "pending"),
            started_at=row.get("started_at", 0.0),
            finished_at=row.get("finished_at"),
            error=row.get("error", ""),
            used_tokens=row.get("used_tokens", 0),
            used_steps=row.get("used_steps", 0),
            metadata=(
                json.loads(row["metadata"])
                if isinstance(row.get("metadata"), str)
                else row.get("metadata", {})
            ),
        )
