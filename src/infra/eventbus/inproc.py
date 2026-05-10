from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator

from src.common.clock import now_ts
from src.common.ids import EVENT_ID
from src.common.types import Event
from src.infra.relational.sqlite import SqliteStore


class InProcessEventBus:
    """进程内 asyncio.Queue 事件总线。"""

    def __init__(
        self, store: SqliteStore | None = None, persist: bool = True, queue_size: int = 1000
    ) -> None:
        self._store = store
        self._persist = persist
        self._queue_size = queue_size
        self._subs: list[_Subscription] = []
        self._lock = asyncio.Lock()
        self._seq_counters: dict[str, int] = defaultdict(int)
        self._closed = False

    async def init(self) -> None:
        if self._persist and self._store:
            await self._store.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    parent_run_id TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    user_id TEXT DEFAULT '',
                    trace_id TEXT DEFAULT '',
                    ts REAL NOT NULL,
                    seq INTEGER NOT NULL DEFAULT 0,
                    payload TEXT DEFAULT '{}',
                    level TEXT DEFAULT 'info',
                    scope TEXT DEFAULT 'public'
                )
            """)

    async def publish(self, event: Event) -> None:
        if self._closed:
            return

        async with self._lock:
            self._seq_counters[event.run_id] += 1
            event.seq = self._seq_counters[event.run_id]

        if not event.event_id:
            event.event_id = EVENT_ID()
        if not event.ts:
            event.ts = now_ts()

        if self._persist and self._store:
            await self._persist_event(event)

        for sub in list(self._subs):
            if sub.matches(event):
                try:
                    sub.queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    async def _persist_event(self, event: Event) -> None:
        import json

        assert self._store is not None
        try:
            await self._store.execute(
                """INSERT OR IGNORE INTO events(event_id, type, run_id, parent_run_id,
                   session_id, user_id, trace_id, ts, seq, payload, level, scope)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                event.event_id,
                event.type,
                event.run_id,
                event.parent_run_id or "",
                event.session_id,
                event.user_id,
                event.trace_id,
                event.ts,
                event.seq,
                json.dumps(event.payload),
                event.level,
                event.scope,
            )
        except Exception:
            pass

    def subscribe(self, filter: dict | None = None) -> _SubContext:
        return _SubContext(self, filter or {})

    async def replay(self, run_id: str, since_seq: int = 0) -> AsyncIterator[Event]:
        if not self._persist or not self._store:
            return

        rows = await self._store.fetch_all(
            """SELECT * FROM events WHERE run_id = ? AND seq > ?
               ORDER BY seq""",
            run_id,
            since_seq,
        )
        for r in rows:
            yield _row_to_event(r)

    async def shutdown(self, timeout: float = 30.0) -> None:
        self._closed = True
        for sub in list(self._subs):
            await sub.aclose()
        self._subs.clear()


class _Subscription:
    def __init__(self, filter: dict, queue_size: int = 1000) -> None:
        self.filter = filter
        self.queue: asyncio.Queue[Event] = asyncio.Queue(queue_size)
        self._closed = False

    def matches(self, ev: Event) -> bool:
        for k, v in self.filter.items():
            if getattr(ev, k, None) != v:
                return False
        return True

    async def aclose(self) -> None:
        self._closed = True


class _SubContext:
    def __init__(self, bus: InProcessEventBus, filter: dict) -> None:
        self._bus = bus
        self._sub = _Subscription(filter, bus._queue_size)

    async def __aenter__(self) -> _SubContext:
        self._bus._subs.append(self._sub)
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._bus._subs.remove(self._sub)

    def __aiter__(self) -> _SubContext:
        return self

    async def __anext__(self) -> Event:
        while not self._sub._closed:
            try:
                ev = await self._sub.queue.get()
                return ev
            except Exception:
                raise StopAsyncIteration from None
        raise StopAsyncIteration

    async def aclose(self) -> None:
        await self._sub.aclose()
        if self._sub in self._bus._subs:
            self._bus._subs.remove(self._sub)


def _row_to_event(row: dict) -> Event:
    import json

    return Event(
        event_id=row["event_id"],
        type=row["type"],
        run_id=row["run_id"],
        parent_run_id=row.get("parent_run_id") or None,
        session_id=row.get("session_id", ""),
        user_id=row.get("user_id", ""),
        trace_id=row.get("trace_id", ""),
        ts=row["ts"],
        seq=row["seq"],
        payload=json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"],
        level=row.get("level", "info"),
        scope=row.get("scope", "public"),
    )
