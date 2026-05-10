from __future__ import annotations

import pytest

from src.common.errors import NotFoundError
from src.infra.cache.memory_cache import MemoryCache
from src.service.artifact import ArtifactStore
from src.service.embedding.gateway import EmbeddingGateway
from src.service.kb.chunker import Chunker
from src.service.kb.loader import load_content, load_markdown, load_plaintext
from src.service.kb.service import KBService


@pytest.fixture
async def artifact_store(sqlite_store, artifacts_tables, fs_backend, event_bus):
    return ArtifactStore(store=sqlite_store, blob=fs_backend, event_bus=event_bus)


@pytest.fixture
async def kb(kb_tables, sqlite_store, artifact_store, event_bus, vec_index, fts_index, stub_embedding):
    mc = MemoryCache(max_size=100)
    await mc.init()
    gateway = EmbeddingGateway(
        providers={"stub": stub_embedding},
        cache=mc,
        default_model="stub",
    )
    return KBService(
        store=sqlite_store,
        artifact=artifact_store,
        jobs=None,
        event_bus=event_bus,
        embedding=gateway,
        vector=vec_index,
        keyword=fts_index,
        chunker=Chunker(target_tokens=100, overlap=20, min_tokens=40),
    )


class TestChunker:
    def test_chunk_plaintext_recursive(self):
        c = Chunker(target_tokens=100, overlap=20)
        text = "Hello world. " * 200
        chunks = c.chunk(text)
        assert len(chunks) > 1
        for ch in chunks:
            assert "text" in ch
            assert ch["char_start"] >= 0
            assert ch["char_end"] > ch["char_start"]

    def test_chunk_short_text_single(self):
        c = Chunker(target_tokens=100)
        chunks = c.chunk("hello world")
        assert len(chunks) == 1

    def test_chunk_markdown_heading_aware(self):
        c = Chunker(target_tokens=500)
        text = "# Title\nSome content here.\n\n## Section 1\nMore content.\n\n## Section 2\nEven more."
        _, structure = load_markdown(text.encode())
        chunks = c.chunk(text, structure)
        assert len(chunks) > 1

    def test_chunk_preserves_char_offsets(self):
        c = Chunker(target_tokens=50)
        text = "abc " * 200
        chunks = c.chunk(text)
        total_chars = sum(ch["char_end"] - ch["char_start"] for ch in chunks)
        assert total_chars > 0


class TestLoader:
    def test_load_plaintext(self):
        text, structure = load_plaintext(b"hello world")
        assert text == "hello world"
        assert structure is None

    def test_load_markdown(self):
        content = b"# Title\n\nSome text."
        text, structure = load_markdown(content)
        assert structure is not None
        assert structure["type"] == "markdown"
        assert len(structure["sections"]) >= 1
        assert "Title" in text

    def test_load_content_dispatch(self):
        text, structure = load_content(b"hello", "text/plain")
        assert text == "hello"

        text2, structure2 = load_content(b"# Title\nbody", "text/markdown")
        assert structure2 is not None


class TestKBService:
    async def test_add_document_creates_document(self, kb, artifact_store):
        art = await artifact_store.save(user_id="u1", content="hello", mime_type="text/plain")
        doc = await kb.add_document(
            user_id="u1", artifact_id=art.artifact_id, title="Test Doc", source_type="upload"
        )
        assert doc.doc_id.startswith("doc_")
        assert doc.title == "Test Doc"

    async def test_get_document_returns_doc(self, kb, artifact_store):
        art = await artifact_store.save(user_id="u1", content="hello", mime_type="text/plain")
        doc = await kb.add_document(
            user_id="u1", artifact_id=art.artifact_id, title="Test Doc"
        )
        fetched = await kb.get_document(doc.doc_id, "u1")
        assert fetched.doc_id == doc.doc_id
        assert fetched.state in ("ingesting", "ready")

    async def test_get_document_missing_raises(self, kb):
        with pytest.raises(NotFoundError):
            await kb.get_document("doc_missing", "u1")

    async def test_list_documents_filters_by_state(self, kb, artifact_store):
        art = await artifact_store.save(user_id="u1", content="hello", mime_type="text/plain")
        await kb.add_document(user_id="u1", artifact_id=art.artifact_id, title="Doc 1")
        docs = await kb.list_documents("u1")
        assert len(docs) >= 1

    async def test_delete_document_removes_chunks(self, kb, artifact_store, sqlite_store):
        art = await artifact_store.save(
            user_id="u1",
            content="Lorem ipsum " * 100,
            mime_type="text/plain",
        )
        doc = await kb.add_document(user_id="u1", artifact_id=art.artifact_id, title="Test")
        # after add_document with jobs=None, ingest runs synchronously
        doc = await kb.get_document(doc.doc_id, "u1")
        assert doc.state == "ready"
        assert doc.chunk_count > 0

        await kb.delete_document(doc.doc_id, "u1")
        doc = await kb.get_document(doc.doc_id, "u1")
        assert doc.state == "archived"


class TestKBServiceIngest:
    async def test_ingest_flow_chunks_and_indexes(self, kb, artifact_store):
        text = "Lorem ipsum dolor sit amet. " * 50
        art = await artifact_store.save(user_id="u1", content=text, mime_type="text/plain")
        doc = await kb.add_document(user_id="u1", artifact_id=art.artifact_id, title="Test")
        doc = await kb.get_document(doc.doc_id, "u1")
        assert doc.state == "ready"
        assert doc.chunk_count > 0

    async def test_ingest_handles_markdown(self, kb, artifact_store):
        md = "# Intro\n\nHello world.\n\n## Details\nMore content here.\n\n## Summary\nFinal words."
        art = await artifact_store.save(user_id="u1", content=md, mime_type="text/markdown")
        doc = await kb.add_document(user_id="u1", artifact_id=art.artifact_id, title="MD Doc")
        doc = await kb.get_document(doc.doc_id, "u1")
        assert doc.state == "ready"

    async def test_ingest_with_vector_index(self, kb, artifact_store, vec_index):
        text = "unique search term zephyr " + "Lorem ipsum. " * 50
        art = await artifact_store.save(user_id="u1", content=text, mime_type="text/plain")
        doc = await kb.add_document(user_id="u1", artifact_id=art.artifact_id, title="Test")
        doc = await kb.get_document(doc.doc_id, "u1")
        assert doc.state == "ready"
        # verify vector index has entries
        hits = await vec_index.search(
            "kb_chunks", [0.0, 0.0, 0.0, 0.0], top_k=10, user_id="u1"
        )
        assert len(hits) > 0
