# 20 · Service 层设计

> **职责**：基于 Infra 提供业务语义服务。**位置**：`src/service/`

---

## 1. 模块清单

| 服务 | 职责 | 依赖 |
|---|---|---|
| **SessionService** | 消息持久化 + rolling summary | RelationalStore + Cache |
| **MemoryService** | 8 类长期记忆 + 混合检索 + MMR | Vector+Keyword+Embedding+Jobs |
| **ArtifactStore** | 大对象存储 + content-hash 去重 + GC | StorageBackend + RelationalStore |
| **KBService** | 文档管理 + 异步 ingest | Artifact+Jobs+Embedding+Vector+Keyword |
| **Retriever** | 统一检索（KB+Memory）+ Hybrid + MMR | Memory+Vector+Keyword |
| **EmbeddingGateway** | embedding 调用 + 批 + 缓存 | Cache + 第三方 SDK |
| **SkillRegistry** | SKILL.md 加载 | 文件系统 |

---

## 2. SessionService

### Schema（`migrations/010_session.sql`）

```sql
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW(),
    message_count INT DEFAULT 0,
    summary TEXT DEFAULT '',
    summary_covers_until_seq INT DEFAULT 0,
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX sessions_user_active ON sessions(user_id, last_active_at DESC);

CREATE TABLE session_messages (
    msg_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    seq INT NOT NULL,
    run_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls JSONB DEFAULT '[]',
    tool_call_id TEXT DEFAULT '',
    name TEXT DEFAULT '',
    artifact_refs JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    UNIQUE(session_id, seq)
);
CREATE INDEX session_msgs_seq ON session_messages(session_id, seq);
```

### 接口

```python
class SessionService:
    SUMMARY_TRIGGER_MSGS = 30
    KEEP_RECENT_AFTER_SUMMARY = 10

    def __init__(self, *, store, cache, event_bus, jobs, llm):
        self._store = store; self._cache = cache
        self._event_bus = event_bus; self._jobs = jobs; self._llm = llm

    async def create(self, user_id, title="") -> SessionInfo: ...
    async def get(self, session_id, user_id) -> SessionInfo | None: ...
    async def list_by_user(self, user_id, limit=50, cursor=None): ...
    async def append_message(self, session_id, user_id, msg, run_id=None) -> SessionMessage: ...
    async def append_messages(self, session_id, user_id, msgs, run_id) -> list[SessionMessage]: ...
    async def get_recent(self, session_id, user_id, limit=50, before_seq=None): ...
    async def get_window_for_context(self, session_id, user_id, limit=20) -> SessionWindow: ...
    async def maybe_summarize(self, session_id, user_id) -> bool: ...
    async def _compress_session(self, session_id, user_id): ...


@dataclass(slots=True)
class SessionWindow:
    summary: str
    summary_covers_until_seq: int
    messages: list[SessionMessage]
```

### 关键算法

```python
async def get_window_for_context(self, session_id, user_id, limit=20):
    info = await self.get(session_id, user_id)
    if not info: return SessionWindow("", 0, [])
    rows = await self._store.fetch_all("""
        SELECT * FROM session_messages
        WHERE session_id=$1 AND user_id=$2 AND seq > $3
        ORDER BY seq DESC LIMIT $4
    """, session_id, user_id, info.summary_covers_until_seq, limit)
    msgs = self._ensure_tool_pairs([self._row_to_msg(r) for r in reversed(rows)])
    return SessionWindow(info.summary, info.summary_covers_until_seq, msgs)


def _ensure_tool_pairs(self, msgs):
    """从前往后扫，孤立的 tool 消息丢弃；
    assistant.tool_calls 缺对应 tool 消息的，清空 tool_calls 字段。"""
    ...


async def maybe_summarize(self, session_id, user_id):
    info = await self.get(session_id, user_id)
    pending = info.message_count - info.summary_covers_until_seq
    if pending < self.SUMMARY_TRIGGER_MSGS: return False
    await self._jobs.enqueue("session.compress",
        {"session_id": session_id, "user_id": user_id}, max_retries=2)
    return True


async def _compress_session(self, session_id, user_id):
    info = await self.get(session_id, user_id)
    end_seq = info.message_count - self.KEEP_RECENT_AFTER_SUMMARY
    if end_seq <= info.summary_covers_until_seq: return
    rows = await self._store.fetch_all("""
        SELECT * FROM session_messages
        WHERE session_id=$1 AND seq>$2 AND seq<=$3 ORDER BY seq ASC
    """, session_id, info.summary_covers_until_seq, end_seq)
    new_text = "\n".join(f"[{r['role']}] {r['content']}" for r in rows)
    prompt = f"已有摘要：\n{info.summary or '（暂无）'}\n\n新增对话：\n{new_text}\n\n融入并保留关键事实/决定/偏好，500 字内。"
    new_summary = await self._llm.complete_simple(prompt, max_tokens=600)
    await self._store.execute("""
        UPDATE sessions SET summary=$1, summary_covers_until_seq=$2,
            last_active_at=NOW() WHERE session_id=$3
    """, new_summary, end_seq, session_id)
    await self._event_bus.publish(Event(
        type=EventType.SESSION_SUMMARIZED,
        session_id=session_id, user_id=user_id, ts=now_ts(),
        payload={"covers_until": end_seq},
    ))
```

### 测试

```
- test_create_and_get
- test_append_assigns_monotonic_seq
- test_append_messages_atomic_in_run
- test_get_recent_excludes_summarized
- test_window_protects_tool_pairs
- test_maybe_summarize_triggers_when_threshold
- test_compress_updates_summary_and_covers_until
```

---

## 3. ArtifactStore

### Schema（`migrations/011_artifact.sql`）

```sql
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    storage_uri TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    mime_type TEXT NOT NULL,
    encoding TEXT DEFAULT 'utf-8',
    summary TEXT DEFAULT '',
    preview TEXT,
    source_type TEXT NOT NULL,
    source_run_id TEXT,
    source_session_id TEXT,
    source_tool_name TEXT,
    source_call_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ DEFAULT NOW(),
    access_count INT DEFAULT 0,
    state TEXT DEFAULT 'active',
    expires_at TIMESTAMPTZ,
    pinned BOOLEAN DEFAULT FALSE,
    title TEXT DEFAULT '',
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX artifacts_user ON artifacts(user_id, created_at DESC);
CREATE INDEX artifacts_hash ON artifacts(user_id, content_hash);
CREATE INDEX artifacts_expires ON artifacts(expires_at)
    WHERE state='active' AND expires_at IS NOT NULL;

CREATE TABLE artifact_blob_refs (
    user_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    storage_uri TEXT NOT NULL,
    refcount INT NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, content_hash)
);
```

### 接口

```python
class ArtifactStore:
    INLINE_THRESHOLD = 4096

    def __init__(self, *, store, blob, event_bus, summarizer=None):
        ...

    async def save(self, *, user_id, content, mime_type, source_type,
                   summary=None, title="", tags=None,
                   source_run_id=None, source_session_id=None,
                   source_tool_name=None, source_call_id=None,
                   ttl_sec=None, metadata=None) -> Artifact: ...

    async def save_stream(self, *, user_id, stream, mime_type, ...) -> Artifact: ...
    async def get_metadata(self, artifact_id, user_id) -> Artifact | None: ...
    async def get_content(self, artifact_id, user_id) -> bytes: ...
    async def get_text(self, artifact_id, user_id) -> str: ...
    async def get_stream(self, artifact_id, user_id): ...
    async def get_signed_url(self, artifact_id, user_id, expires_in=3600) -> str: ...
    async def update(self, artifact_id, user_id, *, title=None, tags=None,
                     pinned=None, expires_at=None) -> None: ...
    async def archive(self, artifact_id, user_id) -> None: ...
    async def delete(self, artifact_id, user_id) -> None: ...
    async def gc(self, batch_size=100) -> int: ...
    async def render_reference(self, artifact: Artifact) -> str: ...
```

### save 流程（去重 + refcount）

```python
async def save(self, *, user_id, content, mime_type, source_type, **opts):
    data = content.encode("utf-8") if isinstance(content, str) else content
    content_hash = hashlib.sha256(data).hexdigest()
    size = len(data)

    async with self._store.transaction() as tx:
        existing = await tx.fetch_one("""
            SELECT * FROM artifact_blob_refs WHERE user_id=$1 AND content_hash=$2
        """, user_id, content_hash)

        if existing:
            storage_uri = existing["storage_uri"]
            await tx.execute("""
                UPDATE artifact_blob_refs SET refcount = refcount + 1
                WHERE user_id=$1 AND content_hash=$2
            """, user_id, content_hash)
        else:
            key = f"{user_id}/{content_hash[:2]}/{content_hash}"
            storage_uri = await self._blob.put(key, data, mime_type)
            await tx.execute("""
                INSERT INTO artifact_blob_refs(user_id, content_hash, storage_uri, refcount)
                VALUES ($1,$2,$3,1)
            """, user_id, content_hash, storage_uri)

        summary = opts.get("summary")
        if summary is None and self._summarizer:
            summary = await self._summarizer.summarize(data, mime_type)

        artifact_id = ARTIFACT_ID()
        await tx.execute("""INSERT INTO artifacts(...) VALUES (...)""", ...)

    artifact = await self.get_metadata(artifact_id, user_id)
    await self._event_bus.publish(Event(
        type=EventType.ARTIFACT_CREATED,
        run_id=opts.get("source_run_id", ""),
        user_id=user_id, ts=now_ts(),
        payload={"artifact_id": artifact_id, "size": size, "mime": mime_type},
    ))
    return artifact
```

### GC 两阶段

```python
async def gc(self, batch_size=100):
    deleted_blobs = 0
    # Phase 1: 标记过期 → state=deleted + refcount-1
    expired = await self._store.fetch_all("""
        SELECT artifact_id FROM artifacts
        WHERE state='active' AND pinned=FALSE
          AND expires_at IS NOT NULL AND expires_at < NOW()
        LIMIT $1
    """, batch_size)
    for r in expired:
        await self._do_delete(r["artifact_id"])

    # Phase 2: 删除 refcount=0 的 blob
    orphans = await self._store.fetch_all("""
        SELECT user_id, content_hash, storage_uri FROM artifact_blob_refs
        WHERE refcount <= 0 LIMIT $1
    """, batch_size)
    for r in orphans:
        try:
            await self._blob.delete(self._extract_key_from_uri(r["storage_uri"]))
            await self._store.execute("""
                DELETE FROM artifact_blob_refs WHERE user_id=$1 AND content_hash=$2
            """, r["user_id"], r["content_hash"])
            deleted_blobs += 1
        except Exception as e:
            log.error("blob_delete_failed", error=str(e))
    return deleted_blobs
```

### Summarizer

```python
class ArtifactSummarizer:
    def __init__(self, llm): self._llm = llm

    async def summarize(self, data, mime_type):
        if mime_type.startswith("text/") or mime_type == "application/json":
            text = data.decode("utf-8", errors="replace")
            return text if len(text) <= 500 else await self._summarize_text(text)
        if mime_type == "application/pdf":
            ...  # 调 pdf skill 抽前 N 页
        if mime_type.startswith("image/"):
            return f"[image, {mime_type}, size={len(data)} bytes]"
        return f"[binary, {mime_type}, size={len(data)} bytes]"

    async def _summarize_text(self, text, max_tokens=200):
        prompt = f"用 100 字以内总结以下内容核心要点：\n\n{text[:8000]}"
        return await self._llm.complete_simple(prompt, max_tokens=max_tokens)
```

### LLM 引用语法

```python
async def render_reference(self, artifact):
    return (f'<artifact id="{artifact.artifact_id}" '
            f'mime="{artifact.mime_type}" size="{artifact.size_bytes}">\n'
            f'摘要：{artifact.summary}\n'
            f'调用 read_artifact(artifact_id="{artifact.artifact_id}") 获取全文\n'
            f'</artifact>')
```

---

## 4. EmbeddingGateway

```python
class EmbeddingGateway:
    def __init__(self, *, providers, cache, default_model,
                 batch_size=32, cache_ttl=30*86400):
        ...

    async def embed(self, texts, model=None) -> list[list[float]]:
        model = model or self._default
        provider = self._providers[model]

        keys = [self._cache_key(model, t) for t in texts]
        cached = await asyncio.gather(*[self._cache.get(k) for k in keys])
        results = list(cached)

        missing_idx = [i for i, v in enumerate(results) if v is None]
        if not missing_idx: return results

        missing_texts = [texts[i] for i in missing_idx]
        for batch_start in range(0, len(missing_texts), self._batch_size):
            batch = missing_texts[batch_start:batch_start+self._batch_size]
            vectors = await provider.embed(batch)
            for j, vec in enumerate(vectors):
                idx = missing_idx[batch_start + j]
                results[idx] = vec
                await self._cache.set(keys[idx], vec, ttl=self._cache_ttl)
        return results

    async def embed_one(self, text, model=None):
        return (await self.embed([text], model))[0]

    def get_dim(self, model=None) -> int:
        return self._providers[model or self._default].dim

    @staticmethod
    def _cache_key(model, text):
        return f"emb:{model}:{hashlib.sha256(text.encode()).hexdigest()}"


class EmbeddingProvider(Protocol):
    dim: int
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddingProvider:
    def __init__(self, api_key, model="text-embedding-3-small"):
        self._client = AsyncOpenAI(api_key=api_key); self._model = model
        self.dim = 1536 if "small" in model else 3072
    async def embed(self, texts):
        resp = await self._client.embeddings.create(model=self._model, input=texts)
        return [d.embedding for d in resp.data]


class BgeEmbeddingProvider:
    def __init__(self, model_name="BAAI/bge-small-zh-v1.5"):
        from FlagEmbedding import FlagModel
        self._model = FlagModel(model_name, use_fp16=False); self.dim = 512
    async def embed(self, texts):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self._model.encode(texts).tolist())
```

---

## 5. KBService

### Schema（`migrations/012_kb.sql`）

```sql
CREATE TABLE kb_documents (
    doc_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_uri TEXT DEFAULT '',
    tags JSONB DEFAULT '[]',
    state TEXT DEFAULT 'ingesting',
    chunk_count INT DEFAULT 0,
    error TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE kb_chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES kb_documents(doc_id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    seq INT NOT NULL,
    text TEXT NOT NULL,
    token_count INT DEFAULT 0,
    embedding_model TEXT NOT NULL,
    char_start INT DEFAULT 0,
    char_end INT DEFAULT 0,
    heading_path JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX kb_chunks_doc ON kb_chunks(doc_id, seq);
CREATE INDEX kb_chunks_user ON kb_chunks(user_id);
```

### 接口 + ingest

```python
class KBService:
    def __init__(self, *, store, artifact, jobs, event_bus,
                 embedding, vector, keyword, chunker):
        ...

    async def add_document(self, *, user_id, artifact_id, title,
                           source_type, source_uri="", tags=None) -> KBDocument:
        doc_id = KB_DOC_ID()
        await self._store.execute("""
            INSERT INTO kb_documents(doc_id, user_id, artifact_id, title,
                source_type, source_uri, tags, state)
            VALUES ($1,$2,$3,$4,$5,$6,$7,'ingesting')
        """, doc_id, user_id, artifact_id, title, source_type, source_uri, tags or [])
        await self._jobs.enqueue("kb.ingest", {
            "doc_id": doc_id, "user_id": user_id, "artifact_id": artifact_id,
        }, max_retries=3)
        return await self.get_document(doc_id, user_id)

    async def ingest(self, doc_id, user_id, artifact_id):
        try:
            artifact = await self._artifact.get_metadata(artifact_id, user_id)
            content = await self._artifact.get_content(artifact_id, user_id)
            text, structure = self._load(content, artifact.mime_type)
            chunks = self._chunker.chunk(text, structure)
            texts = [c["text"] for c in chunks]
            vectors = await self._embedding.embed(texts)
            model = self._embedding._default

            async with self._store.transaction() as tx:
                for i, (c, vec) in enumerate(zip(chunks, vectors)):
                    chunk_id = KB_CHUNK_ID()
                    await tx.execute("""INSERT INTO kb_chunks(...) VALUES (...)""",
                        chunk_id, doc_id, user_id, i, c["text"],
                        c["token_count"], model, c["char_start"], c["char_end"],
                        c["heading_path"])
                    await self._vector.upsert("kb_chunk", [VectorItem(
                        id=chunk_id, vector=vec, user_id=user_id,
                        metadata={"doc_id": doc_id, "seq": i})])
                    await self._keyword.index("kb_chunk", chunk_id, c["text"],
                        user_id, metadata={"doc_id": doc_id, "seq": i})
                await tx.execute("""
                    UPDATE kb_documents SET state='ready', chunk_count=$2,
                        updated_at=NOW() WHERE doc_id=$1
                """, doc_id, len(chunks))

            await self._event_bus.publish(Event(
                type=EventType.KB_DOC_READY, user_id=user_id, ts=now_ts(),
                payload={"doc_id": doc_id, "chunk_count": len(chunks)},
            ))
        except Exception as e:
            await self._store.execute("""
                UPDATE kb_documents SET state='failed', error=$2 WHERE doc_id=$1
            """, doc_id, str(e))
            raise

    def _load(self, content, mime_type) -> tuple[str, dict]:
        # text/plain | markdown | html(trafilatura) | pdf(pdf skill)
        ...
```

### Chunker

```python
class Chunker:
    def __init__(self, target_tokens=512, overlap=64, min_tokens=128):
        self._target, self._overlap, self._min = target_tokens, overlap, min_tokens

    def chunk(self, text, structure) -> list[dict]:
        if structure.get("type") == "markdown":
            return self._chunk_markdown(text, structure)
        return self._chunk_recursive(text)

    def _chunk_markdown(self, text, structure):
        """按 heading 切分，过长二次切，保留 heading_path。"""
    def _chunk_recursive(self, text):
        """段落 → 句子 → 字符 递归切分，重叠 overlap tokens。"""
```

### Worker

```python
class KBIngestWorker:
    async def run(self, stop):
        listen_task = asyncio.create_task(self._listen_loop(stop))
        try:
            while not stop.is_set():
                job = await self._jobs.claim_next(self._worker_id,
                    job_types=["kb.ingest"], lease_sec=600)
                if not job:
                    try: await asyncio.wait_for(self._wake.wait(), timeout=5)
                    except asyncio.TimeoutError: pass
                    self._wake.clear(); continue
                try:
                    await self._kb.ingest(**job.payload)
                    await self._jobs.mark_done(job.job_id)
                except Exception as e:
                    await self._jobs.mark_failed(job.job_id, str(e))
        finally:
            listen_task.cancel()
```

---

## 6. MemoryService

### Schema（`migrations/013_memory.sql`）

```sql
CREATE TABLE memory_records (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_run_id TEXT,
    source_session_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_recalled_at TIMESTAMPTZ,
    occurred_at TIMESTAMPTZ,
    confidence DOUBLE PRECISION DEFAULT 1.0,
    importance DOUBLE PRECISION DEFAULT 0.5,
    recall_count INT DEFAULT 0,
    state TEXT DEFAULT 'active',
    related_memory_ids JSONB DEFAULT '[]',
    artifact_refs JSONB DEFAULT '[]',
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}'
);
CREATE INDEX mem_user_kind ON memory_records(user_id, kind, state);
CREATE INDEX mem_user_active_recall ON memory_records(
    user_id, state, last_recalled_at DESC NULLS LAST
) WHERE state = 'active';
CREATE INDEX mem_tags ON memory_records USING GIN(tags);
```

### 写入（含去重/冲突）

```python
async def remember(self, *, user_id, kind, content,
                   source_type="explicit", importance=0.5, confidence=1.0, **opts):
    vec = await self._embedding.embed_one(content)

    # 1. 同 kind 高相似度 → merge
    similar = await self._vector.search("memory", vec, top_k=3,
        user_id=user_id, filter={"kind": kind})
    for hit in similar:
        if hit.score > 0.92:
            existing = await self._get_record(hit.id, user_id)
            return await self._merge(existing, content, importance)

    # 2. 事实型记忆冲突标记
    if kind in (MemoryKind.PROFILE, MemoryKind.PREFERENCE, MemoryKind.FACT):
        conflict = await self._check_conflict_via_llm(user_id, kind, content, similar)
        if conflict:
            await self.update(conflict.memory_id, user_id, state="contradicted")

    # 3. 写入 + 三索引同事务
    memory_id = MEMORY_ID()
    async with self._store.transaction() as tx:
        await tx.execute("""INSERT INTO memory_records(...) VALUES (...)""", ...)
        await self._vector.upsert("memory", [VectorItem(
            id=memory_id, vector=vec, user_id=user_id, metadata={"kind": kind})])
        await self._keyword.index("memory", memory_id, content, user_id,
            metadata={"kind": kind})

    await self._event_bus.publish(Event(
        type=EventType.MEMORY_WRITTEN, user_id=user_id, ts=now_ts(),
        payload={"memory_id": memory_id, "kind": kind}))
    return await self._get_record(memory_id, user_id)
```

### 检索（Hybrid + Recency + MMR）

```python
async def recall(self, user_id, query, *, top_k=10, kinds=None, diversify=True):
    vec = await self._embedding.embed_one(query)

    # 1. 并行召回
    vec_hits, kw_hits = await asyncio.gather(
        self._vector.search("memory", vec, top_k * 3, user_id),
        self._keyword.search("memory", query, top_k * 3, user_id),
    )

    # 2. 归一化融合
    fused = self._fuse_hits(vec_hits, kw_hits, vec_weight=0.7, kw_weight=0.3)

    # 3. recency boost + importance
    records = await self._batch_get_records([h.id for h in fused], user_id)
    for h, r in zip(fused, records):
        if r and r.last_recalled_at:
            days = (now_ts() - r.last_recalled_at) / 86400
            recency = math.exp(-days / 30)
            h.score = h.score * 0.8 + recency * 0.2 + r.importance * 0.1

    # 4. kind 过滤
    if kinds:
        fused = [h for h, r in zip(fused, records) if r and r.kind in kinds]

    # 5. MMR
    selected = (await self._mmr(fused, vec, top_k, lambda_=0.5)) if diversify \
               else sorted(fused, key=lambda x: -x.score)[:top_k]

    # 6. 更新 last_recalled_at
    if selected:
        await self._store.execute("""
            UPDATE memory_records
            SET last_recalled_at=NOW(), recall_count=recall_count+1
            WHERE memory_id = ANY($1)
        """, [h.chunk_id for h in selected])
    return selected
```

### Extractor

```python
class MemoryExtractor:
    EXTRACT_PROMPT = """从用户对话摘要中提取应记忆的事实。
输出 JSON 数组：{kind, content, importance, confidence}
- kind: profile/preference/fact/event/task/reminder/relation
- content: 第三人称陈述句
- importance/confidence: 0~1
只提取有保留价值的。

摘要：
{summary}"""
    def __init__(self, llm): self._llm = llm
    async def extract(self, summary):
        raw = await self._llm.complete_simple(
            self.EXTRACT_PROMPT.format(summary=summary), max_tokens=1000)
        try: return json.loads(raw)
        except json.JSONDecodeError: return []
```

---

## 7. Retriever（统一检索）

```python
class Retriever:
    def __init__(self, *, memory, kb_vector, kb_keyword, embedding, store):
        ...

    async def search(self, q: RetrievalQuery) -> list[RetrievalHit]:
        tasks = []
        if "memory" in q.channels: tasks.append(self._search_memory(q))
        if "kb"     in q.channels: tasks.append(self._search_kb(q))
        results = await asyncio.gather(*tasks)
        merged = [h for hits in results for h in hits]
        merged.sort(key=lambda h: -h.score)
        merged = merged[:q.top_k * 2]

        if q.diversify:
            qvec = await self._embedding.embed_one(q.text)
            merged = await self._mmr_select(merged, qvec, q.top_k)
        else:
            merged = merged[:q.top_k]
        return merged

    async def _search_kb(self, q):
        qvec = await self._embedding.embed_one(q.text)
        vec_hits, kw_hits = await asyncio.gather(
            self._kb_vector.search("kb_chunk", qvec, q.top_k * 3, q.user_id),
            self._kb_keyword.search("kb_chunk", q.text, q.top_k * 3, q.user_id),
        )
        fused = self._fuse(vec_hits, kw_hits, vec_w=0.7, kw_w=0.3)
        chunks = await self._batch_get_chunks([h.id for h in fused[:q.top_k * 2]], q.user_id)
        return [
            RetrievalHit(channel="kb", chunk_id=c.chunk_id, doc_id=c.doc_id,
                text=c.text, score=hit.score,
                citation={"doc_id": c.doc_id, "heading_path": c.heading_path,
                          "char_start": c.char_start, "char_end": c.char_end})
            for hit, c in zip(fused, chunks) if c
        ]

    async def _search_memory(self, q):
        return await self._memory.recall(q.user_id, q.text, top_k=q.top_k)
```

---

## 8. ServiceContainer 装配

```python
@dataclass
class ServiceContainer:
    session: SessionService
    memory: MemoryService
    artifact: ArtifactStore
    kb: KBService
    retriever: Retriever
    embedding: EmbeddingGateway
    skill: SkillRegistry


async def build_services(infra, llm, cfg) -> ServiceContainer:
    embedding = EmbeddingGateway(
        providers=_build_embedding_providers(cfg),
        cache=infra.cache, default_model=cfg.embedding["model"])
    artifact = ArtifactStore(store=infra.relational, blob=infra.blob,
        event_bus=infra.eventbus, summarizer=ArtifactSummarizer(llm))
    kb = KBService(store=infra.relational, artifact=artifact, jobs=infra.jobs,
        event_bus=infra.eventbus, embedding=embedding,
        vector=infra.vector, keyword=infra.keyword, chunker=Chunker())
    memory = MemoryService(store=infra.relational, vector=infra.vector,
        keyword=infra.keyword, embedding=embedding, jobs=infra.jobs,
        event_bus=infra.eventbus, llm=llm, extractor=MemoryExtractor(llm))
    session = SessionService(store=infra.relational, cache=infra.cache,
        event_bus=infra.eventbus, jobs=infra.jobs, llm=llm)
    retriever = Retriever(memory=memory, kb_vector=infra.vector,
        kb_keyword=infra.keyword, embedding=embedding, store=infra.relational)
    skill = SkillRegistry(path=cfg.agent.skills_path); await skill.load()

    return ServiceContainer(session=session, memory=memory, artifact=artifact,
        kb=kb, retriever=retriever, embedding=embedding, skill=skill)
```

---

## 9. 集成测试 + docker-compose

```
tests/integration/service/
├── conftest.py
├── test_session_e2e.py
├── test_artifact_e2e.py
├── test_kb_e2e.py
├── test_memory_e2e.py
└── test_retriever_e2e.py
```

```yaml
# docker-compose.test.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment: {POSTGRES_PASSWORD: test, POSTGRES_DB: agent_test}
    ports: ["5433:5432"]
  redis:
    image: redis:7-alpine
    ports: ["6380:6379"]
```

---

## 10. 错误处理约定

- Service 方法**只抛 AgentError 子类**，不暴露底层 asyncpg/redis 异常
- artifact_id 不存在 → `NotFoundError`
- 越权 → `PermissionError`
- 资源冲突 → `ConflictError`
- 基础设施异常 → 包装成 `InfraError`