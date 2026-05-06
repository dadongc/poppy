from __future__ import annotations

import pytest

from src.common.clock import now_ts
from src.common.errors import NotFoundError
from src.service.artifact import ArtifactStore, ArtifactSummarizer
from src.service.llm_protocol import StubLLM


@pytest.fixture
async def store(sqlite_store, artifacts_tables, fs_backend, event_bus):
    return ArtifactStore(
        store=sqlite_store,
        blob=fs_backend,
        event_bus=event_bus,
    )


@pytest.fixture
async def store_with_summarizer(sqlite_store, artifacts_tables, fs_backend, event_bus, stub_llm):
    return ArtifactStore(
        store=sqlite_store,
        blob=fs_backend,
        event_bus=event_bus,
        summarizer=ArtifactSummarizer(stub_llm),
    )


class TestArtifactSave:
    async def test_save_returns_artifact_with_id(self, store):
        art = await store.save(user_id="u1", content="hello", mime_type="text/plain")
        assert art.artifact_id.startswith("atf_")
        assert art.size_bytes == 5

    async def test_save_stores_blob(self, store):
        art = await store.save(user_id="u1", content="hello", mime_type="text/plain")
        data = await store.get_content(art.artifact_id, "u1")
        assert data == b"hello"

    async def test_dedup_hash_reuses_storage(self, store):
        art1 = await store.save(user_id="u1", content="same content", mime_type="text/plain")
        art2 = await store.save(user_id="u1", content="same content", mime_type="text/plain")
        assert art1.storage_uri == art2.storage_uri
        assert art1.content_hash == art2.content_hash

    async def test_dedup_increments_refcount(self, store, sqlite_store):
        await store.save(user_id="u1", content="same", mime_type="text/plain")
        await store.save(user_id="u1", content="same", mime_type="text/plain")
        row = await sqlite_store.fetch_one(
            "SELECT refcount FROM artifact_blob_refs WHERE user_id = ?", "u1"
        )
        assert row is not None
        assert row["refcount"] == 2

    async def test_save_with_ttl(self, store):
        art = await store.save(user_id="u1", content="x", ttl_sec=3600)
        assert art.expires_at is not None
        assert art.expires_at > now_ts()

    async def test_save_bytes_content(self, store):
        art = await store.save(user_id="u1", content=b"\x00\x01\x02", mime_type="application/octet-stream")
        data = await store.get_content(art.artifact_id, "u1")
        assert data == b"\x00\x01\x02"

    async def test_save_with_summarizer_auto_summary(self, store_with_summarizer):
        art = await store_with_summarizer.save(
            user_id="u1",
            content="x" * 600,
            mime_type="text/plain",
        )
        assert "stub summary" in art.summary


class TestArtifactGet:
    async def test_get_metadata_returns_artifact(self, store):
        art = await store.save(user_id="u1", content="hello", mime_type="text/plain", title="doc")
        fetched = await store.get_metadata(art.artifact_id, "u1")
        assert fetched.title == "doc"
        assert fetched.mime_type == "text/plain"

    async def test_get_content_returns_bytes(self, store):
        art = await store.save(user_id="u1", content="hello")
        data = await store.get_content(art.artifact_id, "u1")
        assert data == b"hello"

    async def test_get_text_returns_string(self, store):
        art = await store.save(user_id="u1", content="hello")
        text = await store.get_text(art.artifact_id, "u1")
        assert text == "hello"

    async def test_get_missing_raises_not_found(self, store):
        with pytest.raises(NotFoundError):
            await store.get_metadata("atf_missing", "u1")

    async def test_get_updates_access_tracking(self, store):
        art = await store.save(user_id="u1", content="hello")
        await store.get_content(art.artifact_id, "u1")
        fetched = await store.get_metadata(art.artifact_id, "u1")
        assert fetched.access_count == 1


class TestArtifactUpdate:
    async def test_update_title_and_tags(self, store):
        art = await store.save(user_id="u1", content="hello")
        await store.update(art.artifact_id, "u1", title="new", tags=["tag1"])
        fetched = await store.get_metadata(art.artifact_id, "u1")
        assert fetched.title == "new"
        assert fetched.tags == ["tag1"]

    async def test_archive_changes_state(self, store):
        art = await store.save(user_id="u1", content="hello")
        await store.archive(art.artifact_id, "u1")
        fetched = await store.get_metadata(art.artifact_id, "u1")
        assert fetched.state == "archived"

    async def test_delete_soft_deletes(self, store):
        art = await store.save(user_id="u1", content="hello")
        await store.delete(art.artifact_id, "u1")
        fetched = await store.get_metadata(art.artifact_id, "u1")
        assert fetched.state == "deleted"


class TestArtifactGC:
    async def test_gc_expired_artifacts(self, store, sqlite_store):
        art = await store.save(user_id="u1", content="hello", ttl_sec=0)
        # manually set expires_at to past
        await sqlite_store.execute(
            "UPDATE artifacts SET expires_at = ? WHERE artifact_id = ?",
            1.0,
            art.artifact_id,
        )
        deleted = await store.gc()
        fetched = await store.get_metadata(art.artifact_id, "u1")
        assert fetched.state == "deleted"

    async def test_gc_preserves_pinned(self, store, sqlite_store):
        art = await store.save(user_id="u1", content="hello", ttl_sec=0)
        await sqlite_store.execute(
            "UPDATE artifacts SET expires_at = ?, pinned = 1 WHERE artifact_id = ?",
            1.0,
            art.artifact_id,
        )
        await store.gc()
        fetched = await store.get_metadata(art.artifact_id, "u1")
        assert fetched.state == "active"

    async def test_gc_deletes_orphan_blobs(self, store, sqlite_store, fs_backend):
        art = await store.save(user_id="u1", content="hello")
        await store.delete(art.artifact_id, "u1")
        # now refcount should be 0
        deleted = await store.gc()
        # verify blob is gone
        key = art.storage_uri.split("://", 1)[1] if "://" in art.storage_uri else art.storage_uri
        exists = await fs_backend.exists(key)
        assert not exists


class TestArtifactSummarizer:
    async def test_summarize_short_text_returns_directly(self, stub_llm):
        s = ArtifactSummarizer(stub_llm)
        result = await s.summarize(b"short text", "text/plain")
        assert result == "short text"

    async def test_summarize_long_text_calls_llm(self, stub_llm):
        s = ArtifactSummarizer(stub_llm)
        result = await s.summarize(b"x" * 600, "text/plain")
        assert "stub summary" in result

    async def test_summarize_image_returns_placeholder(self, stub_llm):
        s = ArtifactSummarizer(stub_llm)
        result = await s.summarize(b"\x00" * 100, "image/png")
        assert "image" in result

    async def test_summarize_binary_returns_placeholder(self, stub_llm):
        s = ArtifactSummarizer(stub_llm)
        result = await s.summarize(b"\x00" * 100, "application/octet-stream")
        assert "binary" in result


class TestRenderReference:
    async def test_render_reference_xml_format(self, store):
        art = await store.save(user_id="u1", content="hello", title="mydoc")
        xml = await store.render_reference(art)
        assert art.artifact_id in xml
        assert "<artifact" in xml
        assert "read_artifact" in xml
