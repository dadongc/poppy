# 34 · EventBus + RunRegistry

> 这两者是 Runtime 层的核心：**EventBus** 负责事件分发（驱动 SSE + 异步任务）,**RunRegistry** 负责 Run 生命周期与父子关系（驱动取消级联与状态查询）。

实现位置：
- `src/runtime/event_bus.py`
- `src/runtime/run_registry.py`

> 注：底层 `InProcessEventBus` 实现已在 `10-infra.md` 给出;本文聚焦**业务语义、订阅模式、与 Run 协作**。

---

## 1. EventBus 职责

```
┌────────── publish ──────────┐
│  BaseAgent / Tool / Service │ ──→ EventBus ──→ 多个 Subscriber
└─────────────────────────────┘            │
                                           ├─→ SSE Adapter（推送给浏览器）
                                           ├─→ EventPersister（写 PG events）
                                           ├─→ MetricsCollector（埋点统计）
                                           └─→ 业务 Listener（如 Memory 触发器）
```

- 进程内 `asyncio.Queue` 实现,**至少一次** 投递语义
- 订阅者按 filter 接收事件子集
- run 内单调 `seq`（用于 SSE 回放）

---

## 2. EventBus 接口

```python
# src/runtime/event_bus.py
from typing import AsyncIterator, Callable
from src.common.types import Event

class EventFilter:
    """订阅过滤器。"""
    run_id: str | None = None
    user_id: str | None = None
    types: set[str] | None = None      # None 表示全订阅
    scope: str | None = None           # "public" / "internal" / None=both
    since_seq: int = 0                 # 用于回放

class EventBus(Protocol):
    async def publish(self, event: Event) -> None: ...

    def subscribe(self, filter: EventFilter) -> "Subscription": ...

    async def replay(
        self, run_id: str, since_seq: int = 0,
    ) -> AsyncIterator[Event]: ...

    async def shutdown(self, timeout: float = 30.0) -> None: ...


class Subscription:
    """订阅句柄。"""
    async def __aiter__(self) -> AsyncIterator[Event]: ...
    async def aclose(self) -> None: ...
```

---

## 3. InProcessEventBus 实现要点

```python
import asyncio
from collections import defaultdict
from itertools import count

class InProcessEventBus:
    def __init__(self, persister: "EventPersister | None" = None):
        # 每个 run 的 seq 计数器
        self._seq_counters: dict[str, count] = defaultdict(lambda: count(start=1))
        self._subscriptions: list["_Sub"] = []
        self._persister = persister
        self._lock = asyncio.Lock()
        self._closed = False

    async def publish(self, event: Event) -> None:
        if self._closed:
            return
        # 分配 seq（同 run 内单调）
        if event.seq == 0:
            event.seq = next(self._seq_counters[event.run_id])

        # 1. 持久化（异步落盘,不阻塞 publish）
        if self._persister:
            asyncio.create_task(self._persister.write(event))

        # 2. fan-out 到所有匹配的订阅者
        for sub in list(self._subscriptions):
            if sub.matches(event):
                # 非阻塞 put：订阅者慢则丢弃 / 等待（按策略）
                try:
                    sub.queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("subscriber slow, drop", run_id=event.run_id)

    def subscribe(self, filter: EventFilter) -> Subscription:
        sub = _Sub(filter)
        self._subscriptions.append(sub)
        return sub

    async def replay(self, run_id, since_seq=0) -> AsyncIterator[Event]:
        # 从持久化层捞历史事件
        if not self._persister:
            return
        async for ev in self._persister.read(run_id, since_seq):
            yield ev

    async def shutdown(self, timeout=30.0):
        self._closed = True
        for sub in self._subscriptions:
            await sub.aclose()
        # 等待持久化 backlog 落盘
        if self._persister:
            await self._persister.flush(timeout=timeout)


class _Sub:
    def __init__(self, filter: EventFilter):
        self.filter = filter
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        self._closed = False

    def matches(self, ev: Event) -> bool:
        f = self.filter
        if f.run_id and ev.run_id != f.run_id:
            return False
        if f.user_id and ev.user_id != f.user_id:
            return False
        if f.types and ev.type not in f.types:
            return False
        if f.scope and ev.scope != f.scope:
            return False
        if ev.seq <= f.since_seq:
            return False
        return True

    async def __aiter__(self):
        while not self._closed:
            try:
                ev = await self.queue.get()
                yield ev
            except asyncio.CancelledError:
                return

    async def aclose(self):
        self._closed = True
```

---

## 4. EventPersister（写 PG events 表）

### 4.1 表结构

```sql
CREATE TABLE events (
    event_id      TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    parent_run_id TEXT,
    session_id    TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    trace_id      TEXT,
    ts            DOUBLE PRECISION NOT NULL,
    seq           BIGINT NOT NULL,
    payload       JSONB NOT NULL,
    level         TEXT NOT NULL,
    scope         TEXT NOT NULL,
    UNIQUE (run_id, seq)
);
CREATE INDEX idx_events_run_seq ON events (run_id, seq);
CREATE INDEX idx_events_user_ts ON events (user_id, ts DESC);
```

### 4.2 EventPersister 批量落盘

```python
class EventPersister:
    """批量异步写入。事件量大时聚合 flush,减少 IO。"""
    def __init__(self, store: RelationalStore, batch_size=50, flush_interval=0.5):
        self._buf: list[Event] = []
        self._lock = asyncio.Lock()
        self._store = store
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._task = asyncio.create_task(self._loop())

    async def write(self, event: Event):
        async with self._lock:
            self._buf.append(event)
            # 终态事件立即 flush（保证 SSE 回放完整）
            if event.type in TERMINAL_EVENT_TYPES:
                await self._flush_locked()

    async def _loop(self):
        while True:
            await asyncio.sleep(self._flush_interval)
            async with self._lock:
                if self._buf:
                    await self._flush_locked()

    async def _flush_locked(self):
        if not self._buf:
            return
        rows = [self._row(e) for e in self._buf]
        self._buf.clear()
        await self._store.execute_many(INSERT_EVENT_SQL, rows)

    async def read(self, run_id, since_seq=0):
        rows = await self._store.fetch(
            "SELECT * FROM events WHERE run_id=$1 AND seq>$2 ORDER BY seq",
            run_id, since_seq,
        )
        for r in rows:
            yield self._to_event(r)

    async def flush(self, timeout=30):
        async with asyncio.timeout(timeout):
            async with self._lock:
                await self._flush_locked()

TERMINAL_EVENT_TYPES = {
    "run.completed", "run.failed", "run.cancelled", "run.timeout",
}
```

---

## 5. 订阅模式

### 5.1 SSE 订阅（最常用）

```python
# Gateway 内
sub = event_bus.subscribe(EventFilter(
    run_id=run_id,
    types={
        EventType.LLM_TEXT_DELTA, EventType.LLM_TOOL_CALL_START,
        EventType.LLM_TOOL_CALL_DELTA, EventType.LLM_TOOL_CALL_END,
        EventType.TOOL_STARTED, EventType.TOOL_COMPLETED,
        EventType.RUN_COMPLETED, EventType.RUN_FAILED, EventType.RUN_CANCELLED,
    },
    scope="public",
    since_seq=since_seq,
))
async for ev in sub:
    yield format_sse(ev)
```

回放支持：客户端断线重连时带 `Last-Event-ID: <seq>`,Gateway 先从 persister `replay(run_id, since_seq)` 推历史,再切到实时订阅。

### 5.2 业务订阅（Memory 触发器）

```python
# 在 Runtime.start_workers 时注册
async def memory_listener():
    sub = event_bus.subscribe(EventFilter(
        types={EventType.RUN_COMPLETED},
    ))
    async for ev in sub:
        # 触发 memory 提取 job
        await memory_service.extract_async(run_id=ev.run_id)

asyncio.create_task(memory_listener())
```

---

## 6. RunRegistry 职责

```
┌─────────────────────────┐
│  RunRegistry (singleton)│
├─────────────────────────┤
│  runs: {run_id: RunInfo}│
│  closure: 闭包表 (PG)   │  ← 父子关系,O(1) 查所有后代
│  cancel: 级联调用      │
└─────────────────────────┘
```

接口：

```python
class RunRegistry:
    async def register(self, info: RunInfo) -> None: ...
    async def link(self, parent_run_id: str, child_run_id: str) -> None: ...
    async def update(self, run_id: str, **fields) -> None: ...
    async def get(self, run_id: str) -> RunInfo | None: ...
    async def descendants(self, run_id: str) -> list[str]: ...
    async def cancel(self, run_id: str) -> int: ...
        """取消该 run 及所有后代。返回受影响数量。"""
    async def attach_cancel_event(self, run_id: str, ev: asyncio.Event) -> None: ...
```

---

## 7. 闭包表设计

为什么要闭包表?SubAgent 可以多层嵌套（A → B → C → D）,取消 A 要同时取消 B/C/D。递归查 `parent_run_id` 在 PG 上虽然能 CTE 实现,但**闭包表查后代是 O(后代数量)** 一次扫描。

### 表结构

```sql
CREATE TABLE run_closure (
    ancestor   TEXT NOT NULL,
    descendant TEXT NOT NULL,
    depth      INT NOT NULL,
    PRIMARY KEY (ancestor, descendant)
);
CREATE INDEX idx_run_closure_anc ON run_closure (ancestor);
CREATE INDEX idx_run_closure_desc ON run_closure (descendant);

CREATE TABLE runs (
    run_id        TEXT PRIMARY KEY,
    parent_run_id TEXT,
    session_id    TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    agent_name    TEXT NOT NULL,
    state         TEXT NOT NULL,
    started_at    DOUBLE PRECISION NOT NULL,
    finished_at   DOUBLE PRECISION,
    error         TEXT,
    used_tokens   INT NOT NULL DEFAULT 0,
    used_steps    INT NOT NULL DEFAULT 0,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX idx_runs_session ON runs (session_id);
CREATE INDEX idx_runs_user_started ON runs (user_id, started_at DESC);
```

### 插入新 Run（自动维护闭包）

```sql
-- 注册 run（depth=0 自引用）
INSERT INTO runs (run_id, parent_run_id, ...) VALUES ($1, $2, ...);
INSERT INTO run_closure (ancestor, descendant, depth) VALUES ($1, $1, 0);

-- 关联到 parent（如有）
INSERT INTO run_closure (ancestor, descendant, depth)
SELECT ancestor, $child, depth + 1
FROM run_closure
WHERE descendant = $parent;
```

### 查询所有后代

```sql
SELECT descendant FROM run_closure WHERE ancestor = $1 AND depth > 0;
```

---

## 8. RunRegistry 实现

```python
class RunRegistry:
    def __init__(self, store: RelationalStore):
        self._store = store
        # 内存中缓存活跃 run 的 cancel_event 引用（不持久化）
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def register(self, info: RunInfo):
        async with self._store.transaction() as tx:
            await tx.execute(
                """INSERT INTO runs (run_id, parent_run_id, session_id, user_id,
                   agent_name, state, started_at, used_tokens, used_steps, metadata)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                info.run_id, info.parent_run_id, info.session_id, info.user_id,
                info.agent_name, info.state, info.started_at,
                info.used_tokens, info.used_steps, json.dumps(info.metadata),
            )
            # 闭包表：自引用
            await tx.execute(
                "INSERT INTO run_closure (ancestor, descendant, depth) VALUES ($1, $1, 0)",
                info.run_id,
            )
            if info.parent_run_id:
                await tx.execute(
                    """INSERT INTO run_closure (ancestor, descendant, depth)
                       SELECT ancestor, $1, depth + 1
                       FROM run_closure WHERE descendant = $2""",
                    info.run_id, info.parent_run_id,
                )

    async def attach_cancel_event(self, run_id, ev: asyncio.Event):
        async with self._lock:
            self._cancel_events[run_id] = ev

    async def cancel(self, run_id) -> int:
        # 1. 查所有后代（含自身）
        rows = await self._store.fetch(
            "SELECT descendant FROM run_closure WHERE ancestor = $1",
            run_id,
        )
        ids = [r["descendant"] for r in rows]
        # 2. 内存中触发 cancel_event
        async with self._lock:
            for rid in ids:
                ev = self._cancel_events.get(rid)
                if ev and not ev.is_set():
                    ev.set()
        # 3. DB 状态更新（cancel_requested 不一定立即生效,但记录意图）
        await self._store.execute(
            """UPDATE runs SET state='cancelled'
               WHERE run_id = ANY($1::text[]) AND state IN ('pending','running')""",
            ids,
        )
        return len(ids)

    async def update(self, run_id, **fields):
        if not fields:
            return
        cols, vals = [], []
        for i, (k, v) in enumerate(fields.items(), start=2):
            cols.append(f"{k} = ${i}")
            vals.append(v)
        await self._store.execute(
            f"UPDATE runs SET {', '.join(cols)} WHERE run_id = $1",
            run_id, *vals,
        )
        # 终态时清理 cancel_event 引用
        if fields.get("state") in {"completed", "failed", "cancelled", "timeout"}:
            async with self._lock:
                self._cancel_events.pop(run_id, None)

    async def get(self, run_id) -> RunInfo | None:
        row = await self._store.fetchrow("SELECT * FROM runs WHERE run_id = $1", run_id)
        return self._to_info(row) if row else None

    async def descendants(self, run_id) -> list[str]:
        rows = await self._store.fetch(
            "SELECT descendant FROM run_closure WHERE ancestor=$1 AND depth>0",
            run_id,
        )
        return [r["descendant"] for r in rows]
```

---

## 9. Run 状态机

```
       register
   ┌──────────────┐
   │              ↓
[pending] ──→ [running] ──→ [completed]
                  │
                  ├──→ [failed]
                  ├──→ [cancelled]
                  └──→ [timeout]
```

转移规则：
- `pending → running`：BaseAgent.run_loop 开始第一轮 perceive 时
- `running → completed`：模型输出 final_answer 或 ReAct 自然终止
- `running → failed`：任意未捕获异常 / BudgetExceededError
- `running → cancelled`：cancel_event 被外部 set
- `running → timeout`：deadline_at 到达

终态后 `finished_at` 必填。

---

## 10. SSE 回放 + 实时订阅串联

```
Client GET /runs/{id}/events?since_seq=N
   ↓
Gateway:
   1. 查 RunRegistry.get(run_id) — 是否还在 running
   2. async for ev in event_bus.replay(run_id, since_seq=N): yield SSE
   3. 若 run 仍 active：
        sub = event_bus.subscribe(EventFilter(run_id, since_seq=last_seq))
        async for ev in sub: yield SSE
   4. 若 run 已终态：直接关闭 SSE（"event: done"）
```

**断点续传**：客户端断线后重新 `GET /events?since_seq=last_received_seq`,无缝接上。

---

## 11. 单元测试

```
tests/unit/runtime/event_bus/
├── test_publish_fanout.py        # 多订阅者按 filter 分发
├── test_seq_monotonic.py         # 同 run seq 单调
├── test_persistence.py           # 终态事件立即 flush
├── test_replay_then_live.py      # 回放 + 实时无缝串联
├── test_subscriber_slow_drop.py  # 慢订阅者 queue 满策略
└── test_shutdown_drains.py       # shutdown 等待 backlog

tests/unit/runtime/run_registry/
├── test_register_self_closure.py # 闭包表自引用
├── test_link_parent_chain.py     # 三层 nesting → ancestor 包含 root
├── test_descendants_query.py     # 查后代 O(N)
├── test_cancel_cascade.py        # 取消祖先 → 所有 cancel_event.set()
├── test_state_transitions.py     # 非法状态转移拒绝
└── test_terminal_cleanup.py      # 终态后 cancel_event 引用清理
```

---

## 12. 与其他模块契约

| 调用方 | 调用点 | 入参 | 出参 |
|---|---|---|---|
| Runtime.start_run | 创建 run | `RunInfo` | None |
| Orchestrator.spawn_subagent | link 父子 | `parent_id, child_info` | None |
| BaseAgent.run_loop | update used_tokens / state | `run_id, **fields` | None |
| Gateway POST /cancel | 触发取消 | `run_id` | `affected_count` |
| Gateway GET /events | replay + subscribe | `run_id, since_seq` | SSE stream |
| MemoryListener | 监听 run.completed | `EventFilter(types={...})` | None |

---

## 13. 设计 FAQ

**Q: 为什么不用 Redis Pub/Sub?**
A: 单进程内 asyncio.Queue 零序列化、零网络往返。多实例部署再加 Redis Pub/Sub 做跨实例桥接。

**Q: 为什么订阅者满时丢弃而不是阻塞 publisher?**
A: 阻塞会拖慢整个 ReAct 循环。SSE 客户端慢应该自己断线重连+回放,而不是拖累 LLM 流。

**Q: 闭包表 vs 邻接表 vs CTE 递归?**
A: 邻接表查后代要递归 CTE,QPS 高时性能差;闭包表插入贵但查询快。Run 嵌套深度有限（通常 ≤3）,插入成本可接受。

**Q: 为什么 cancel_event 不持久化?**
A: 进程重启后 run 已经丢失上下文,重新启动只能 fail。cancel_event 是进程内信号量,没必要落盘。重启后将所有 running 状态置为 failed("process_restart") 即可。