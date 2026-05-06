from __future__ import annotations

from src.common.types import VectorItem


class TestSqliteVecIndex:
    async def test_upsert_and_search(self, vec_index):
        await vec_index.upsert(
            "test",
            [
                VectorItem(id="a", vector=[1.0, 0.0, 0.0, 0.0], user_id="u1"),
                VectorItem(id="b", vector=[0.0, 1.0, 0.0, 0.0], user_id="u1"),
            ],
        )
        hits = await vec_index.search("test", [1.0, 0.0, 0.0, 0.0], top_k=2, user_id="u1")
        assert len(hits) == 2
        assert hits[0].id == "a"
        assert hits[0].score > hits[1].score

    async def test_search_user_isolation(self, vec_index):
        await vec_index.upsert(
            "test",
            [
                VectorItem(id="a", vector=[1.0, 0.0, 0.0, 0.0], user_id="u1"),
                VectorItem(id="b", vector=[1.0, 0.0, 0.0, 0.0], user_id="u2"),
            ],
        )
        hits_u1 = await vec_index.search("test", [1.0, 0.0, 0.0, 0.0], top_k=5, user_id="u1")
        assert all(h.id == "a" for h in hits_u1)

    async def test_search_namespace_isolation(self, vec_index):
        await vec_index.upsert(
            "ns1",
            [VectorItem(id="a", vector=[1.0, 0.0, 0.0, 0.0], user_id="u1")],
        )
        await vec_index.upsert(
            "ns2",
            [VectorItem(id="b", vector=[0.0, 1.0, 0.0, 0.0], user_id="u1")],
        )
        hits = await vec_index.search("ns1", [1.0, 0.0, 0.0, 0.0], top_k=5, user_id="u1")
        assert all(h.id == "a" for h in hits)

    async def test_delete(self, vec_index):
        await vec_index.upsert(
            "test",
            [
                VectorItem(id="a", vector=[1.0, 0.0, 0.0, 0.0], user_id="u1"),
                VectorItem(id="b", vector=[0.0, 1.0, 0.0, 0.0], user_id="u1"),
            ],
        )
        await vec_index.delete("test", ["a"])
        hits = await vec_index.search("test", [1.0, 0.0, 0.0, 0.0], top_k=5, user_id="u1")
        assert all(h.id != "a" for h in hits)
        assert len(hits) == 1

    async def test_upsert_update(self, vec_index):
        await vec_index.upsert(
            "test",
            [VectorItem(id="a", vector=[1.0, 0.0, 0.0, 0.0], user_id="u1")],
        )
        await vec_index.upsert(
            "test",
            [VectorItem(id="a", vector=[0.0, 1.0, 0.0, 0.0], user_id="u1")],
        )
        hits = await vec_index.search("test", [0.0, 1.0, 0.0, 0.0], top_k=1, user_id="u1")
        assert hits[0].id == "a"
        assert hits[0].score > 0.9
