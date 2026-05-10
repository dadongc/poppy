from __future__ import annotations


class TestFts5Index:
    async def test_index_and_search(self, fts_index):
        await fts_index.index("kb", "doc1", "hello world test document", "u1")
        await fts_index.index("kb", "doc2", "another document about python", "u1")

        hits = await fts_index.search("kb", "world", top_k=5, user_id="u1")
        assert len(hits) >= 1
        assert hits[0].id == "doc1"

    async def test_search_user_isolation(self, fts_index):
        await fts_index.index("kb", "doc1", "hello world", "u1")
        await fts_index.index("kb", "doc2", "hello world", "u2")

        hits = await fts_index.search("kb", "hello", top_k=5, user_id="u1")
        assert all(h.id == "doc1" for h in hits)

    async def test_search_namespace_isolation(self, fts_index):
        await fts_index.index("kb", "doc1", "hello world", "u1")
        await fts_index.index("notes", "doc2", "hello python", "u1")

        hits = await fts_index.search("kb", "hello", top_k=5, user_id="u1")
        assert all(h.id == "doc1" for h in hits)

    async def test_delete(self, fts_index):
        await fts_index.index("kb", "doc1", "hello world", "u1")
        await fts_index.index("kb", "doc2", "hello python", "u1")

        await fts_index.delete("kb", ["doc1"])
        hits = await fts_index.search("kb", "hello", top_k=5, user_id="u1")
        assert all(h.id != "doc1" for h in hits)

    async def test_update_reindex(self, fts_index):
        await fts_index.index("kb", "doc1", "original text", "u1")
        await fts_index.index("kb", "doc1", "updated content here", "u1")

        hits = await fts_index.search("kb", "original", top_k=5, user_id="u1")
        assert len(hits) == 0
        hits = await fts_index.search("kb", "updated", top_k=5, user_id="u1")
        assert len(hits) >= 1
