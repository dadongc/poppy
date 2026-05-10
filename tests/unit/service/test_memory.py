from __future__ import annotations

import pytest

from src.common.errors import NotFoundError
from src.common.types import MemoryKind
from src.infra.cache.memory_cache import MemoryCache
from src.service.embedding.gateway import EmbeddingGateway
from src.service.memory.extractor import MemoryExtractor
from src.service.memory.service import MemoryService


@pytest.fixture
async def mem_svc(memory_tables, sqlite_store, vec_index, fts_index, stub_embedding, event_bus, stub_llm):
    mc = MemoryCache(max_size=100)
    await mc.init()
    gateway = EmbeddingGateway(
        providers={"stub": stub_embedding},
        cache=mc,
        default_model="stub",
    )
    return MemoryService(
        store=sqlite_store,
        vector=vec_index,
        keyword=fts_index,
        embedding=gateway,
        jobs=None,
        event_bus=event_bus,
        llm=stub_llm,
    )


class TestMemoryRemember:
    async def test_remember_creates_record(self, mem_svc):
        r = await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="用户喜欢咖啡")
        assert r.memory_id.startswith("mem_")

    async def test_remember_persists_to_db(self, mem_svc, sqlite_store):
        r = await mem_svc.remember(user_id="u1", kind=MemoryKind.PROFILE, content="用户叫张三")
        row = await sqlite_store.fetch_one(
            "SELECT * FROM memory_records WHERE memory_id = ?", r.memory_id
        )
        assert row is not None
        assert row["content"] == "用户叫张三"

    async def test_remember_indexes_vector(self, mem_svc, vec_index):
        await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="test fact one")
        hits = await vec_index.search(
            "memory", [0.0, 0.0, 0.0, 0.0], top_k=10, user_id="u1"
        )
        assert len(hits) > 0

    async def test_remember_indexes_keyword(self, mem_svc, fts_index):
        await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="unique zephyr keyword")
        hits = await fts_index.search(
            "memory", "unique zephyr", top_k=10, user_id="u1"
        )
        assert len(hits) > 0

    async def test_dedup_low_similarity_creates_new(self, mem_svc):
        # StubEmbedding returns zero vectors, so similarity scores are unreliable.
        # Dedup requires real embedding vectors. With stubs, all memories get
        # separate IDs unless they happen to match on vector search.
        r1 = await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="fact one")
        r2 = await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="fact two")
        assert r1.memory_id != r2.memory_id


class TestMemoryForget:
    async def test_forget_soft_deletes(self, mem_svc):
        r = await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="to be forgotten")
        await mem_svc.forget(r.memory_id, "u1")
        record = await mem_svc._get_record(r.memory_id, "u1")
        assert record is not None
        assert record.state == "deleted"

    async def test_forgotten_not_in_recall(self, mem_svc):
        r = await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="forgotten fact")
        await mem_svc.forget(r.memory_id, "u1")
        results = await mem_svc.recall("u1", "forgotten")
        ids = [m.memory_id for m in results]
        assert r.memory_id not in ids


class TestMemoryRecall:
    async def test_recall_returns_results(self, mem_svc):
        await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="recall test alpha")
        results = await mem_svc.recall("u1", "alpha")
        assert len(results) > 0

    async def test_recall_kind_filter(self, mem_svc):
        await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="fact memory")
        await mem_svc.remember(user_id="u1", kind=MemoryKind.PREFERENCE, content="pref memory")
        results = await mem_svc.recall("u1", "memory", kinds=[MemoryKind.PREFERENCE])
        assert all(r.kind == MemoryKind.PREFERENCE for r in results)

    async def test_recall_updates_recall_count(self, mem_svc, sqlite_store):
        r = await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="count test")
        await mem_svc.recall("u1", "count")
        row = await sqlite_store.fetch_one(
            "SELECT recall_count FROM memory_records WHERE memory_id = ?", r.memory_id
        )
        assert row is not None
        assert row["recall_count"] >= 1

    async def test_recall_vector_retrieval(self, mem_svc):
        await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="python programming language")
        await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="cooking recipes")
        results = await mem_svc.recall("u1", "programming code")
        assert len(results) > 0

    async def test_recall_keyword_retrieval(self, mem_svc):
        await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="xyzzy unique keyword here")
        await mem_svc.remember(user_id="u1", kind=MemoryKind.FACT, content="ordinary content")
        results = await mem_svc.recall("u1", "xyzzy")
        assert len(results) > 0


class TestMemoryExtractor:
    async def test_extract_handles_non_json(self, stub_llm):
        # StubLLM returns "stub summary" which is not JSON
        extractor = MemoryExtractor(stub_llm)
        result = await extractor.extract("some summary")
        assert result == []
