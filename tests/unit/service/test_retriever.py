from __future__ import annotations

import pytest

from src.common.types import RetrievalQuery, MemoryKind
from src.infra.cache.memory_cache import MemoryCache
from src.service.artifact import ArtifactStore
from src.service.embedding.gateway import EmbeddingGateway
from src.service.kb.chunker import Chunker
from src.service.kb.service import KBService
from src.service.memory.service import MemoryService
from src.service.retriever import Retriever


@pytest.fixture
async def retriever(
    kb_tables,
    memory_tables,
    artifacts_tables,
    sqlite_store,
    vec_index,
    fts_index,
    fs_backend,
    event_bus,
    stub_embedding,
    stub_llm,
):
    mc = MemoryCache(max_size=100)
    await mc.init()
    gateway = EmbeddingGateway(
        providers={"stub": stub_embedding}, cache=mc, default_model="stub"
    )
    artifact_store = ArtifactStore(
        store=sqlite_store, blob=fs_backend, event_bus=event_bus
    )
    mem_svc = MemoryService(
        store=sqlite_store,
        vector=vec_index,
        keyword=fts_index,
        embedding=gateway,
        jobs=None,
        event_bus=event_bus,
        llm=stub_llm,
    )
    kb = KBService(
        store=sqlite_store,
        artifact=artifact_store,
        jobs=None,
        event_bus=event_bus,
        embedding=gateway,
        vector=vec_index,
        keyword=fts_index,
        chunker=Chunker(target_tokens=100),
    )
    # populate some data
    await mem_svc.remember(
        user_id="u1", kind=MemoryKind.FACT, content="retriever test memory about programming"
    )
    art = await artifact_store.save(
        user_id="u1",
        content="KB document about machine learning " * 50,
        mime_type="text/plain",
    )
    await kb.add_document(
        user_id="u1", artifact_id=art.artifact_id, title="ML Doc", source_type="upload"
    )
    return Retriever(
        memory=mem_svc,
        kb_vector=vec_index,
        kb_keyword=fts_index,
        embedding=gateway,
        store=sqlite_store,
    )


class TestRetriever:
    async def test_search_memory_channel(self, retriever):
        q = RetrievalQuery(text="programming", user_id="u1", channels=["memory"], top_k=5)
        results = await retriever.search(q)
        assert any(r.channel == "memory" for r in results)

    async def test_search_kb_channel(self, retriever):
        q = RetrievalQuery(text="machine learning", user_id="u1", channels=["kb"], top_k=5)
        results = await retriever.search(q)
        assert any(r.channel == "kb" for r in results)

    async def test_search_both_channels(self, retriever):
        q = RetrievalQuery(text="learning", user_id="u1", channels=["memory", "kb"], top_k=5)
        results = await retriever.search(q)
        channels = {r.channel for r in results}
        assert len(results) > 0

    async def test_top_k_limits(self, retriever):
        q = RetrievalQuery(text="test", user_id="u1", channels=["kb"], top_k=2, diversify=False)
        results = await retriever.search(q)
        assert len(results) <= 2
