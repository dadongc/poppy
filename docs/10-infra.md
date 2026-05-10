# 10 · Infra 层设计

> **职责**：以 Protocol 形式定义所有基础设施接口，提供生产实现（阿里云）和本地开发实现（SQLite/FS/Memory）。
> **强约束**：业务层（Service/Agent/Tool/Gateway）禁止直接 import asyncpg/redis/oss2/sqlite3，必须通过 Protocol。
> **位置**：`src/infra/`

---

## 1. 目录结构

```
src/infra/
├── __init__.py
├── protocols.py              # 所有 Protocol 定义
├── relational/
│   ├── postgres.py           # PostgresStore（生产）
│   └── sqlite.py             # SQLiteStore（本地）
├── vector/
│   ├── pgvector.py           # PgVectorIndex
│   └── sqlite_vec.py
├── keyword/
│   ├── pg_tsvector.py
│   └── fts5.py
├── blob/
│   ├── oss.py                # OssBackend
│   └── filesystem.py
├── cache/
│   ├── redis_cache.py
│   └── memory_cache.py
├── eventbus/
│   └── inproc.py             # InProcessEventBus
├── jobs/
│   └── pg_jobs.py            # PgJobQueue
└── factory.py                # build_infra
```

---

## 2. Protocol 接口契约（src/infra/protocols.py）

```python
from typing import Protocol, AsyncContextManager, Any
from dataclasses import dataclass
from src.common.types import VectorItem, VectorHit, KeywordHit


class RelationalStore(Protocol):
    async def init(self) -> None: ...
    async def close(self) -> None: ...
    async def execute(self, sql: str, *params) -> int: ...
    async def fetch_one(self, sql: str, *params) -> dict | None: ...
    async def fetch_all(self, sql: str, *params) -> list[dict]: ...
    def transaction(self) -> AsyncContextManager["Transaction"]: ...
    async def listen(self, channel: str) -> "AsyncIterator[str]": ...
    async def notify(self, channel: str, payload: str = "") -> None: ...


class Transaction(Protocol):
    async def execute(self, sql: str, *params) -> int: ...
    async def fetch_one(self, sql: str, *params) -> dict | None: ...
    async def fetch_all(self, sql: str, *params) -> list[dict]: ...


class VectorIndex(Protocol):
    async def init(self) -> None: ...
    async def upsert(self, namespace: str, items: list[VectorItem]) -> None: ...
    async def search(self, namespace, query_vec, top_k, user_id, filter=None) -> list[VectorHit]: ...
    async def delete(self, namespace: str, ids: list[str]) -> None: ...


class KeywordIndex(Protocol):
    async def init(self) -> None: ...
    async def index(self, namespace, doc_id, text, user_id, metadata=None) -> None: ...
    async def search(self, namespace, query, top_k, user_id, filter=None) -> list[KeywordHit]: ...
    async def delete(self, namespace, doc_ids) -> None: ...


class StorageBackend(Protocol):
    async def init(self) -> None: ...
    async def put(self, key, data, mime_type="application/octet-stream") -> str: ...
    async def put_stream(self, key, stream, mime_type="application/octet-stream") -> str: ...
    async def get(self, key) -> bytes: ...
    async def get_stream(self, key) -> "AsyncIterator[bytes]": ...
    async def delete(self, key) -> None: ...
    async def exists(self, key) -> bool: ...
    async def get_signed_url(self, key, expires_in: int = 3600) -> str: ...


class Cache(Protocol):
    async def init(self) -> None: ...
    async def get(self, key) -> Any | None: ...
    async def set(self, key, value, ttl: int | None = None) -> None: ...
    async def delete(self, key) -> None: ...
    async def incr(self, key, amount=1, ttl=None) -> int: ...


class EventBus(Protocol):
    async def init(self) -> None: ...
    async def publish(self, event: "Event") -> None: ...
    def subscribe(self, filter: dict | None = None) -> AsyncContextManager["EventSubscription"]: ...


class EventSubscription(Protocol):
    def __aiter__(self) -> "EventSubscription": ...
    async def __anext__(self) -> "Event": ...
    async def close(self) -> None: ...


class JobQueue(Protocol):
    async def init(self) -> None: ...
    async def enqueue(self, job_type, payload, *, priority=0,
                      scheduled_at=None, max_retries=3) -> str: ...
    async def claim_next(self, worker_id, job_types=None, lease_sec=300) -> "Job | None": ...
    async def mark_done(self, job_id) -> None: ...
    async def mark_failed(self, job_id, error, retry: bool = True) -> None: ...
    async def listen(self) -> "AsyncIterator[str]": ...


@dataclass(slots=True, kw_only=True)
class Job:
    job_id: str
    job_type: str
    payload: dict
    retry_count: int
    max_retries: int
    locked_until: float
```

---

## 3. PostgresStore

```python
# src/infra/relational/postgres.py
import asyncpg, json
from typing import AsyncIterator

class PostgresStore:
    def __init__(self, dsn: str, pool_size: int = 10):
        self._dsn = dsn
        self._pool_size = pool_size
        self._pool: asyncpg.Pool | None = None

    async def init(self):
        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=2, max_size=self._pool_size,
            command_timeout=30, init=self._init_conn,
        )

    @staticmethod
    async def _init_conn(conn):
        await conn.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )

    async def close(self):
        if self._pool:
            await self._pool.close()

    async def execute(self, sql, *params) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, *params)
            return int(result.split()[-1]) if result else 0

    async def fetch_one(self, sql, *params):
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return dict(row) if row else None

    async def fetch_all(self, sql, *params):
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [dict(r) for r in rows]

    def transaction(self):
        return _PgTransaction(self._pool)

    async def listen(self, channel) -> AsyncIterator[str]:
        async with self._pool.acquire() as conn:
            queue: asyncio.Queue[str] = asyncio.Queue()
            await conn.add_listener(channel, lambda *args: queue.put_nowait(args[3]))
            try:
                while True:
                    yield await queue.get()
            finally:
                await conn.remove_listener(channel, ...)

    async def notify(self, channel, payload=""):
        async with self._pool.acquire() as conn:
            await conn.execute(f"NOTIFY {channel}, $1", payload)


class _PgTransaction:
    def __init__(self, pool):
        self._pool = pool
        self._conn = None
        self._tx = None

    async def __aenter__(self):
        self._conn = await self._pool.acquire()
        self._tx = self._conn.transaction()
        await self._tx.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                await self._tx.rollback()
            else:
                await self._tx.commit()
        finally:
            await self._pool.release(self._conn)
```

### 必装扩展

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS zhparser;
CREATE TEXT SEARCH CONFIGURATION zhcfg (PARSER = zhparser);
ALTER TEXT SEARCH CONFIGURATION zhcfg
  ADD MAPPING FOR n, v, a, i, e, l, j WITH simple;
```

### Schema 迁移（migrations/001_init.sql + migrator.py）

```sql
CREATE TABLE IF NOT EXISTS _schema_meta (
    version INT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT NOW(),
    description TEXT
);
INSERT INTO _schema_meta(version, description)
VALUES (1, 'init') ON CONFLICT DO NOTHING;
```

```python
async def run_migrations(store, migrations_dir: Path):
    applied = await store.fetch_all("SELECT version FROM _schema_meta")
    applied_versions = {r["version"] for r in applied}
    for f in sorted(migrations_dir.glob("*.sql")):
        version = int(f.name.split("_")[0])
        if version in applied_versions:
            continue
        sql = f.read_text()
        async with store.transaction() as tx:
            await tx.execute(sql)
            await tx.execute(
                "INSERT INTO _schema_meta(version, description) VALUES ($1,$2)",
                version, f.stem,
            )
```

---

## 4. PgVectorIndex

```python
class PgVectorIndex:
    def __init__(self, store, dim: int, metric: str = "cosine"):
        self._store = store
        self._dim = dim
        self._metric = metric
        self._tables: set[str] = set()

    async def _ensure_table(self, namespace):
        if namespace in self._tables: return
        table = f"vec_{namespace}"
        ops = {"cosine": "vector_cosine_ops",
               "l2": "vector_l2_ops",
               "inner_product": "vector_ip_ops"}[self._metric]
        await self._store.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                embedding vector({self._dim}) NOT NULL,
                metadata JSONB DEFAULT '{{}}',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS {table}_user_idx ON {table}(user_id);
            CREATE INDEX IF NOT EXISTS {table}_hnsw_idx ON {table}
                USING hnsw (embedding {ops}) WITH (m=16, ef_construction=64);
        """)
        self._tables.add(namespace)

    async def upsert(self, namespace, items):
        await self._ensure_table(namespace)
        table = f"vec_{namespace}"
        for it in items:
            await self._store.execute(f"""
                INSERT INTO {table}(id, user_id, embedding, metadata)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (id) DO UPDATE
                SET embedding = EXCLUDED.embedding,
                    metadata = EXCLUDED.metadata
            """, it.id, it.user_id, _vec(it.vector), it.metadata)

    async def search(self, namespace, query_vec, top_k, user_id, filter=None):
        await self._ensure_table(namespace)
        table = f"vec_{namespace}"
        rows = await self._store.fetch_all(f"""
            SELECT id, metadata, 1 - (embedding <=> $1) AS score
            FROM {table}
            WHERE user_id = $2
            ORDER BY embedding <=> $1
            LIMIT $3
        """, _vec(query_vec), user_id, top_k)
        return [VectorHit(id=r["id"], score=r["score"], metadata=r["metadata"])
                for r in rows]


def _vec(v): return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
```

### Namespace 约定

| Namespace   | 内容                | 维度                |
| ----------- | ------------------- | ------------------- |
| `memory`    | MemoryRecord 向量   | 由 EmbeddingGateway |
| `kb_chunk`  | KBChunk 向量        | 同上                |

> **维度锁定**：每个 namespace 维度一旦确定不可变，换 embedding 模型要新建 namespace 并 reindex。

---

## 5. PgTsvectorIndex（关键词检索）

```python
class PgTsvectorIndex:
    def __init__(self, store, ts_config: str = "zhcfg"):
        self._store = store
        self._ts_config = ts_config
        self._tables: set[str] = set()

    async def _ensure_table(self, namespace):
        if namespace in self._tables: return
        table = f"kw_{namespace}"
        await self._store.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                text TEXT NOT NULL,
                tsv tsvector GENERATED ALWAYS AS
                    (to_tsvector('{self._ts_config}', text)) STORED,
                metadata JSONB DEFAULT '{{}}'
            );
            CREATE INDEX IF NOT EXISTS {table}_user_idx ON {table}(user_id);
            CREATE INDEX IF NOT EXISTS {table}_tsv_idx ON {table} USING GIN(tsv);
        """)
        self._tables.add(namespace)

    async def search(self, namespace, query, top_k, user_id, filter=None):
        await self._ensure_table(namespace)
        table = f"kw_{namespace}"
        rows = await self._store.fetch_all(f"""
            SELECT id, metadata,
                   ts_rank(tsv, plainto_tsquery('{self._ts_config}', $1)) AS score,
                   ts_headline('{self._ts_config}', text,
                               plainto_tsquery('{self._ts_config}', $1),
                               'MaxWords=20, MinWords=10') AS snippet
            FROM {table}
            WHERE user_id = $2
              AND tsv @@ plainto_tsquery('{self._ts_config}', $1)
            ORDER BY score DESC
            LIMIT $3
        """, query, user_id, top_k)
        return [KeywordHit(id=r["id"], score=r["score"],
                           snippet=r["snippet"], metadata=r["metadata"])
                for r in rows]
```

---

## 6. OssBackend / FilesystemBackend

```python
# src/infra/blob/oss.py
import oss2, asyncio
from concurrent.futures import ThreadPoolExecutor

class OssBackend:
    def __init__(self, *, endpoint, bucket, access_key, secret_key, prefix=""):
        self._auth = oss2.Auth(access_key, secret_key)
        self._bucket = oss2.Bucket(self._auth, endpoint, bucket)
        self._prefix = prefix.rstrip("/")
        self._bucket_name = bucket
        self._executor = ThreadPoolExecutor(max_workers=8)

    async def _run(self, fn, *args, **kw):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, lambda: fn(*args, **kw))

    def _full_key(self, key): return f"{self._prefix}/{key}".lstrip("/")

    async def put(self, key, data, mime_type="application/octet-stream"):
        full = self._full_key(key)
        await self._run(self._bucket.put_object, full, data,
                        headers={"Content-Type": mime_type})
        return f"oss://{self._bucket_name}/{full}"

    async def get(self, key):
        result = await self._run(self._bucket.get_object, self._full_key(key))
        return await self._run(result.read)

    async def get_signed_url(self, key, expires_in=3600):
        return await self._run(self._bucket.sign_url, "GET",
                               self._full_key(key), expires_in)
    # delete / exists / put_stream / get_stream 略
```

```python
# src/infra/blob/filesystem.py
import aiofiles
from pathlib import Path

class FilesystemBackend:
    def __init__(self, root): self._root = Path(root); self._root.mkdir(parents=True, exist_ok=True)
    def _path(self, key):
        p = self._root / key; p.parent.mkdir(parents=True, exist_ok=True); return p

    async def put(self, key, data, mime_type="application/octet-stream"):
        async with aiofiles.open(self._path(key), "wb") as f:
            await f.write(data)
        return f"fs://{self._path(key).absolute()}"

    async def get(self, key):
        async with aiofiles.open(self._path(key), "rb") as f:
            return await f.read()
    # 其余略
```

---

## 7. RedisCache

```python
import redis.asyncio as redis, json
from typing import Any

class RedisCache:
    def __init__(self, url): self._url = url; self._client = None

    async def init(self):
        self._client = redis.from_url(self._url, decode_responses=False)
        await self._client.ping()

    async def get(self, key) -> Any | None:
        raw = await self._client.get(key)
        return json.loads(raw) if raw else None

    async def set(self, key, value, ttl=None):
        data = json.dumps(value)
        if ttl: await self._client.setex(key, ttl, data)
        else:   await self._client.set(key, data)

    async def incr(self, key, amount=1, ttl=None) -> int:
        async with self._client.pipeline() as pipe:
            pipe.incrby(key, amount)
            if ttl: pipe.expire(key, ttl)
            results = await pipe.execute()
        return results[0]
```

---

## 8. InProcessEventBus

```python
# src/infra/eventbus/inproc.py
import asyncio
from collections import defaultdict
from src.common.types import Event
from src.common.ids import EVENT_ID
from src.common.clock import now_ts

class InProcessEventBus:
    def __init__(self, store=None, persist=True, queue_size=1000):
        self._store = store
        self._persist = persist
        self._subs: list[_Subscription] = []
        self._lock = asyncio.Lock()
        self._seq_per_run: dict[str, int] = defaultdict(int)

    async def publish(self, event: Event):
        async with self._lock:
            self._seq_per_run[event.run_id] += 1
            event.seq = self._seq_per_run[event.run_id]
        if not event.event_id: event.event_id = EVENT_ID()
        if not event.ts:       event.ts = now_ts()

        if self._persist and self._store:
            asyncio.create_task(self._persist_event(event))

        for sub in list(self._subs):
            if sub.matches(event):
                try: sub.queue.put_nowait(event)
                except asyncio.QueueFull:
                    log.warning("event_dropped", run_id=event.run_id)

    async def _persist_event(self, event):
        try:
            await self._store.execute("""
                INSERT INTO events(event_id, type, run_id, parent_run_id,
                    session_id, user_id, trace_id, ts, seq, payload, level, scope)
                VALUES ($1,$2,$3,$4,$5,$6,$7,to_timestamp($8),$9,$10,$11,$12)
            """, event.event_id, event.type, event.run_id, event.parent_run_id,
                event.session_id, event.user_id, event.trace_id, event.ts,
                event.seq, event.payload, event.level, event.scope)
        except Exception as e:
            log.error("event_persist_failed", error=str(e))

    def subscribe(self, filter=None):
        return _SubContext(self, filter or {})


class _Subscription:
    def __init__(self, filter, queue_size=1000):
        self.filter = filter
        self.queue: asyncio.Queue[Event] = asyncio.Queue(queue_size)

    def matches(self, ev):
        for k, v in self.filter.items():
            if getattr(ev, k, None) != v: return False
        return True


class _SubContext:
    def __init__(self, bus, filter):
        self._bus = bus
        self._sub = _Subscription(filter)
    async def __aenter__(self):
        self._bus._subs.append(self._sub); return self
    async def __aexit__(self, *exc):
        self._bus._subs.remove(self._sub)
    def __aiter__(self): return self
    async def __anext__(self): return await self._sub.queue.get()
```

`events` 表（migrations/002_events.sql）：

```sql
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    run_id TEXT NOT NULL,
    parent_run_id TEXT,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    trace_id TEXT,
    ts TIMESTAMPTZ NOT NULL,
    seq INT NOT NULL,
    payload JSONB DEFAULT '{}',
    level TEXT DEFAULT 'info',
    scope TEXT DEFAULT 'public'
);
CREATE INDEX events_run_seq ON events(run_id, seq);
CREATE INDEX events_session_ts ON events(session_id, ts DESC);
CREATE INDEX events_user_ts ON events(user_id, ts DESC);
```

---

## 9. PgJobQueue

```python
class PgJobQueue:
    NOTIFY_CHANNEL = "agent_jobs"
    def __init__(self, store): self._store = store

    async def enqueue(self, job_type, payload, *, priority=0,
                      scheduled_at=None, max_retries=3) -> str:
        from src.common.ids import JOB_ID
        job_id = JOB_ID()
        scheduled_ts = scheduled_at or now_ts()
        await self._store.execute("""
            INSERT INTO async_jobs(job_id, job_type, payload, priority,
                scheduled_at, max_retries)
            VALUES ($1,$2,$3,$4,to_timestamp($5),$6)
        """, job_id, job_type, payload, priority, scheduled_ts, max_retries)
        await self._store.notify(self.NOTIFY_CHANNEL, job_type)
        return job_id

    async def claim_next(self, worker_id, job_types=None, lease_sec=300):
        types_clause = "AND job_type = ANY($2)" if job_types else ""
        async with self._store.transaction() as tx:
            row = await tx.fetch_one(f"""
                SELECT * FROM async_jobs
                WHERE state='pending' AND scheduled_at<=NOW() {types_clause}
                ORDER BY priority DESC, scheduled_at ASC
                LIMIT 1 FOR UPDATE SKIP LOCKED
            """, *([job_types] if job_types else []))
            if not row: return None
            await tx.execute("""
                UPDATE async_jobs SET state='running', locked_by=$1,
                    started_at=NOW(),
                    locked_until=NOW() + ($2 || ' seconds')::INTERVAL
                WHERE job_id=$3
            """, worker_id, lease_sec, row["job_id"])
            return Job(job_id=row["job_id"], job_type=row["job_type"],
                       payload=row["payload"], retry_count=row["retry_count"],
                       max_retries=row["max_retries"],
                       locked_until=now_ts() + lease_sec)

    async def mark_done(self, job_id):
        await self._store.execute("""
            UPDATE async_jobs SET state='done', finished_at=NOW()
            WHERE job_id=$1
        """, job_id)

    async def mark_failed(self, job_id, error, retry=True):
        if retry:
            await self._store.execute("""
                UPDATE async_jobs
                SET state = CASE
                    WHEN retry_count + 1 >= max_retries THEN 'failed'
                    ELSE 'pending'
                END,
                retry_count = retry_count + 1,
                error = $2,
                scheduled_at = NOW() + (LEAST(retry_count + 1, 5) * INTERVAL '30 seconds')
                WHERE job_id = $1
            """, job_id, error)
        else:
            await self._store.execute("""
                UPDATE async_jobs SET state='failed', error=$2, finished_at=NOW()
                WHERE job_id=$1
            """, job_id, error)

    async def listen(self):
        async for payload in self._store.listen(self.NOTIFY_CHANNEL):
            yield payload
```

`async_jobs` 表（migrations/003_jobs.sql）：

```sql
CREATE TABLE IF NOT EXISTS async_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    priority INT DEFAULT 0,
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    scheduled_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error TEXT,
    locked_by TEXT,
    locked_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX jobs_pending ON async_jobs(state, priority DESC, scheduled_at)
    WHERE state = 'pending';
CREATE INDEX jobs_type ON async_jobs(job_type, state);
```

---

## 10. InfraFactory

```python
@dataclass
class Infra:
    relational: RelationalStore
    vector: VectorIndex
    keyword: KeywordIndex
    blob: StorageBackend
    cache: Cache
    eventbus: EventBus
    jobs: JobQueue


async def build_infra(cfg: InfraConfig) -> Infra:
    # Relational
    if cfg.relational["backend"] == "postgres":
        rel = PostgresStore(dsn=cfg.relational["dsn"],
                            pool_size=cfg.relational.get("pool_size", 10))
    elif cfg.relational["backend"] == "sqlite":
        rel = SqliteStore(path=cfg.relational["path"])
    await rel.init()
    await run_migrations(rel, Path("migrations"))

    # Vector
    if cfg.vector["backend"] == "pgvector":
        vec = PgVectorIndex(rel, dim=cfg.vector["dim"],
                            metric=cfg.vector.get("metric","cosine"))
    elif cfg.vector["backend"] == "sqlite-vec":
        vec = SqliteVecIndex(rel, dim=cfg.vector["dim"])
    await vec.init()

    # Keyword / Blob / Cache / EventBus / Jobs 同理
    ...
    return Infra(relational=rel, vector=vec, keyword=kw, blob=blob,
                 cache=cache, eventbus=bus, jobs=jobs)
```

---

## 11. 单元测试清单

```
tests/unit/infra/
├── conftest.py
├── test_postgres_store.py
├── test_pgvector_index.py
├── test_pg_tsvector_index.py
├── test_oss_backend.py
├── test_filesystem_backend.py
├── test_redis_cache.py
├── test_memory_cache.py
├── test_inproc_eventbus.py
├── test_pg_jobs.py
└── test_migrator.py

tests/integration/infra/
├── test_pg_real.py
├── test_redis_real.py
└── test_oss_real.py
```

```python
# 示例：test_inproc_eventbus.py
@pytest.mark.asyncio
async def test_publish_subscribe_basic(store):
    bus = InProcessEventBus(store=store, persist=False)
    received = []
    async def consumer():
        async with bus.subscribe(filter={"run_id": "r1"}) as sub:
            async for ev in sub:
                received.append(ev)
                if len(received) == 2: break
    task = asyncio.create_task(consumer())
    await asyncio.sleep(0.05)
    await bus.publish(Event(event_id="e1", type="t", run_id="r1", ...))
    await bus.publish(Event(event_id="e2", type="t", run_id="r2", ...))  # 应过滤
    await bus.publish(Event(event_id="e3", type="t", run_id="r1", ...))
    await asyncio.wait_for(task, timeout=1.0)
    assert [e.event_id for e in received] == ["e1", "e3"]


@pytest.mark.asyncio
async def test_seq_monotonic(store):
    bus = InProcessEventBus(store=store, persist=False)
    e1 = Event(...); e2 = Event(...); e3 = Event(...)
    await bus.publish(e1); await bus.publish(e2); await bus.publish(e3)
    assert (e1.seq, e2.seq, e3.seq) == (1, 2, 3)


@pytest.mark.integration
async def test_pgvector_search_relevance(pg_store):
    vec = PgVectorIndex(pg_store, dim=4)
    await vec.upsert("test", [
        VectorItem(id="a", vector=[1,0,0,0], user_id="u1"),
        VectorItem(id="b", vector=[0,1,0,0], user_id="u1"),
        VectorItem(id="c", vector=[0.9,0.1,0,0], user_id="u1"),
        VectorItem(id="d", vector=[1,0,0,0], user_id="u2"),
    ])
    hits = await vec.search("test", [1,0,0,0], top_k=3, user_id="u1")
    assert hits[0].id == "a" and hits[1].id == "c"
    assert "d" not in [h.id for h in hits]
```

---

## 12. 配置示例

`config/dev.yaml`：

```yaml
infra:
  relational: {backend: sqlite, path: ./data/sqlite/main.db}
  vector:     {backend: sqlite-vec, dim: 384}
  keyword:    {backend: fts5}
  blob:       {backend: filesystem, root: ./data/artifacts}
  cache:      {backend: memory, max_items: 10000}
  eventbus:   {backend: inproc, persist: true}
```

`config/prod.yaml`：

```yaml
infra:
  relational:
    backend: postgres
    dsn: postgresql://user:pwd@xxx.pg.rds.aliyuncs.com:5432/agent
    pool_size: 10
  vector: {backend: pgvector, dim: 512, metric: cosine}
  keyword: {backend: pg_tsvector, config: zhcfg}
  blob:
    backend: oss
    params:
      endpoint: oss-cn-hangzhou-internal.aliyuncs.com
      bucket: my-agent-artifacts
      access_key: ${OSS_ACCESS_KEY}
      secret_key: ${OSS_SECRET_KEY}
      prefix: artifacts
  cache: {backend: redis, url: redis://xxx.redis.aliyuncs.com:6379/0}
  eventbus: {backend: inproc, persist: true}
```

---

## 13. 常见坑

1. **asyncpg 的 JSONB 默认是 str** —— 必须 `set_type_codec` 注册 json 编解码
2. **pgvector 维度** —— HNSW 索引上限 2000；text-embedding-3-large(3072) 需降维
3. **OSS 网络** —— ECS 必须用 internal endpoint 访问 OSS，避免公网流量费
4. **zhparser 词典** —— 默认词典对专业领域召回一般，可加自定义词典
5. **Redis 连接池** —— `redis.from_url` 自带连接池；要 `await client.aclose()` 关闭
6. **PG 连接数** —— RDS 基础版 max_connections=100，asyncpg pool_size 控制 ≤10
7. **LISTEN 长连接** —— 监听通道独占一个 conn，不能放回 pool

---

## 14. 监控埋点（预留）

```python
metrics.histogram("infra.pg.query.duration_ms", duration, sql_kind="select")
metrics.counter("infra.cache.hit_total", op="get").inc()
metrics.counter("infra.cache.miss_total", op="get").inc()
metrics.gauge("infra.pg.pool.in_use").set(pool.size - pool.idle_size)
```