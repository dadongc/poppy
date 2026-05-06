from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.common.types import VectorItem
from src.infra.jobs.pg_jobs import NOTIFY_CHANNEL, PgJobQueue
from src.infra.keyword.pg_tsvector import PgTsvectorIndex, _match_filter
from src.infra.vector.pgvector import PgVectorIndex, _vec


class TestVecHelper:
    def test_vec_format(self):
        assert _vec([1.0, 0.5, 0.0]) == "[1.000000,0.500000,0.000000]"

    def test_vec_single(self):
        assert _vec([3.141592]) == "[3.141592]"


class TestMatchFilter:
    def test_match_all(self):
        meta = {"a": 1, "b": 2}
        assert _match_filter(meta, {"a": 1}) is True

    def test_no_match(self):
        meta = {"a": 1}
        assert _match_filter(meta, {"a": 2}) is False

    def test_missing_key(self):
        meta = {"a": 1}
        assert _match_filter(meta, {"b": 2}) is False


class TestPgVectorIndex:
    @staticmethod
    def make_store():
        store = MagicMock()
        store.fetch_all = AsyncMock()
        store.execute = AsyncMock()
        return store

    async def test_ensure_table_creates_once(self):
        store = self.make_store()
        idx = PgVectorIndex(store, dim=4)
        await idx._ensure_table("test_ns")
        await idx._ensure_table("test_ns")
        assert store.execute.call_count == 3  # table + 2 indexes

    async def test_search_returns_hits(self):
        store = self.make_store()
        store.fetch_all.return_value = [
            {"id": "a", "score": 0.95, "metadata": {"tag": "x"}},
            {"id": "b", "score": 0.3, "metadata": {}},
        ]
        idx = PgVectorIndex(store, dim=4)
        idx._tables.add("ns")
        hits = await idx.search("ns", [1.0, 0, 0, 0], top_k=2, user_id="u1")
        assert len(hits) == 2
        assert hits[0].id == "a"
        assert hits[0].score == 0.95

    async def test_search_with_filter(self):
        store = self.make_store()
        store.fetch_all.return_value = [
            {"id": "a", "score": 0.9, "metadata": {"lang": "en"}},
            {"id": "b", "score": 0.8, "metadata": {"lang": "zh"}},
        ]
        idx = PgVectorIndex(store, dim=4)
        idx._tables.add("ns")
        hits = await idx.search("ns", [1.0, 0, 0, 0], top_k=5, user_id="u1", filter={"lang": "zh"})
        assert len(hits) == 1
        assert hits[0].id == "b"

    async def test_upsert_batches(self):
        store = self.make_store()
        idx = PgVectorIndex(store, dim=4)
        idx._tables.add("ns")
        items = [
            VectorItem(id="a", vector=[1.0, 0, 0, 0], user_id="u1"),
            VectorItem(id="b", vector=[0, 1.0, 0, 0], user_id="u1"),
        ]
        await idx.upsert("ns", items)
        assert store.execute.call_count == 2

    async def test_delete(self):
        store = self.make_store()
        idx = PgVectorIndex(store, dim=4)
        idx._tables.add("ns")
        await idx.delete("ns", ["a", "b"])
        assert store.execute.call_count == 2


class TestPgTsvectorIndex:
    @staticmethod
    def make_store():
        store = MagicMock()
        store.fetch_all = AsyncMock()
        store.execute = AsyncMock()
        return store

    async def test_ensure_table_creates_once(self):
        store = self.make_store()
        idx = PgTsvectorIndex(store)
        await idx._ensure_table("ns")
        await idx._ensure_table("ns")
        assert store.execute.call_count == 3  # table + 2 indexes

    async def test_index_inserts(self):
        store = self.make_store()
        idx = PgTsvectorIndex(store)
        idx._tables.add("ns")
        await idx.index("ns", "doc1", "hello", "u1", {"tag": "x"})
        assert store.execute.call_count == 2  # delete + insert

    async def test_search_returns_hits(self):
        store = self.make_store()
        store.fetch_all.return_value = [
            {"id": "d1", "score": 0.8, "snippet": "<b>hello</b> world", "metadata": {}},
        ]
        idx = PgTsvectorIndex(store)
        idx._tables.add("ns")
        hits = await idx.search("ns", "hello", top_k=5, user_id="u1")
        assert len(hits) == 1
        assert hits[0].id == "d1"
        assert "<b>hello</b>" in hits[0].snippet

    async def test_search_with_filter(self):
        store = self.make_store()
        store.fetch_all.return_value = [
            {"id": "d1", "score": 0.8, "snippet": "", "metadata": {"lang": "en"}},
            {"id": "d2", "score": 0.6, "snippet": "", "metadata": {"lang": "zh"}},
        ]
        idx = PgTsvectorIndex(store)
        idx._tables.add("ns")
        hits = await idx.search("ns", "hello", top_k=5, user_id="u1", filter={"lang": "zh"})
        assert len(hits) == 1
        assert hits[0].id == "d2"

    async def test_delete(self):
        store = self.make_store()
        idx = PgTsvectorIndex(store)
        idx._tables.add("ns")
        await idx.delete("ns", ["d1", "d2"])
        assert store.execute.call_count == 2


class TestPgJobQueue:
    @staticmethod
    def make_store():
        store = MagicMock()
        store.fetch_all = AsyncMock()
        store.execute = AsyncMock()
        store.notify = AsyncMock()
        store.listen = MagicMock()
        return store

    async def test_enqueue(self):
        store = self.make_store()
        jq = PgJobQueue(store)
        jid = await jq.enqueue("scan", {"url": "x"})
        assert jid.startswith("job_")
        store.execute.assert_called_once()
        store.notify.assert_called_once_with(NOTIFY_CHANNEL, "scan")

    async def test_claim_next_returns_job(self):
        store = self.make_store()
        store.fetch_all.return_value = [
            {
                "job_id": "job_001",
                "job_type": "scan",
                "payload": {},
                "retry_count": 0,
                "max_retries": 3,
            }
        ]
        jq = PgJobQueue(store)
        job = await jq.claim_next("worker1")
        assert job is not None
        assert job.job_id == "job_001"
        assert job.job_type == "scan"

    async def test_claim_next_empty(self):
        store = self.make_store()
        store.fetch_all.return_value = []
        jq = PgJobQueue(store)
        job = await jq.claim_next("worker1")
        assert job is None

    async def test_claim_next_with_job_types(self):
        store = self.make_store()
        store.fetch_all.return_value = [
            {
                "job_id": "j1",
                "job_type": "scan",
                "payload": {},
                "retry_count": 0,
                "max_retries": 3,
            }
        ]
        jq = PgJobQueue(store)
        job = await jq.claim_next("w1", job_types=["scan", "fetch"])
        assert job is not None
        call_args = store.fetch_all.call_args
        assert call_args[0][1] == ["scan", "fetch"]

    async def test_mark_done(self):
        store = self.make_store()
        jq = PgJobQueue(store)
        await jq.mark_done("job_001")
        store.execute.assert_called_once()

    async def test_mark_failed_no_retry(self):
        store = self.make_store()
        jq = PgJobQueue(store)
        await jq.mark_failed("job_001", "fatal", retry=False)
        store.execute.assert_called_once()


class TestOssBackend:
    async def test_full_key_with_prefix(self):
        from src.infra.blob.oss import OssBackend

        with patch("src.infra.blob.oss.oss2") as _mock_oss2:
            be = OssBackend(
                endpoint="oss-cn-hangzhou.aliyuncs.com",
                bucket="test-bucket",
                access_key_id="ak",
                access_key_secret="sk",
                prefix="test-prefix",
            )
            assert be._full_key("my/file.txt") == "test-prefix/my/file.txt"

    async def test_full_key_no_prefix(self):
        from src.infra.blob.oss import OssBackend

        with patch("src.infra.blob.oss.oss2") as _mock_oss2:
            be = OssBackend(
                endpoint="oss-cn-hangzhou.aliyuncs.com",
                bucket="test-bucket",
                access_key_id="ak",
                access_key_secret="sk",
                prefix="",
            )
            assert be._full_key("file.txt") == "file.txt"

    async def test_put_returns_uri(self):
        from src.infra.blob.oss import OssBackend

        with patch("src.infra.blob.oss.oss2") as _mock_oss2:
            be = OssBackend(
                endpoint="oss-cn-hangzhou.aliyuncs.com",
                bucket="test-bucket",
                access_key_id="ak",
                access_key_secret="sk",
                prefix="test-prefix",
            )
            be._run = AsyncMock(return_value=None)  # type: ignore[method-assign]
            uri = await be.put("key.txt", b"data", mime_type="text/plain")
            assert uri == "oss://test-bucket/test-prefix/key.txt"


class TestRedisCache:
    def make_client_mock(self):
        client = MagicMock()
        client.ping = AsyncMock(return_value=True)
        client.get = AsyncMock(return_value=None)
        client.set = AsyncMock(return_value=True)
        client.setex = AsyncMock(return_value=True)
        client.delete = AsyncMock(return_value=1)
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=[5])
        pipe.__aenter__ = AsyncMock(return_value=pipe)
        pipe.__aexit__ = AsyncMock(return_value=None)
        client.pipeline.return_value = pipe
        return client

    def make_cache(self):
        from src.infra.cache.redis_cache import RedisCache

        cache = RedisCache(url="redis://localhost:6379/0")
        cache._client = self.make_client_mock()
        return cache

    async def test_get_returns_value(self):
        c = self.make_cache()
        c._client.get.return_value = json.dumps({"a": 1})
        val = await c.get("k")
        assert val == {"a": 1}

    async def test_get_miss(self):
        c = self.make_cache()
        c._client.get.return_value = None
        val = await c.get("k")
        assert val is None

    async def test_set_with_ttl(self):
        c = self.make_cache()
        await c.set("k", "v", ttl=60)
        c._client.setex.assert_called_once_with("k", 60, json.dumps("v"))

    async def test_set_without_ttl(self):
        c = self.make_cache()
        await c.set("k", "v")
        c._client.set.assert_called_once_with("k", json.dumps("v"))

    async def test_incr(self):
        c = self.make_cache()
        val = await c.incr("counter", amount=2)
        assert val == 5

    async def test_incr_with_ttl(self):
        c = self.make_cache()
        val = await c.incr("counter", amount=1, ttl=300)
        assert val == 5


class TestFactoryProduction:
    async def test_rejects_unsupported_relational(self):
        from src.infra.factory import build_infra

        with pytest.raises(ValueError, match="Unsupported relational backend"):
            await build_infra(
                {
                    "relational": {"backend": "mysql"},
                    "vector": {},
                    "keyword": {},
                    "blob": {},
                    "cache": {},
                    "eventbus": {},
                },
                run_migrations_flag=False,
            )

    async def test_rejects_unsupported_blob(self):
        import tempfile
        from pathlib import Path

        from src.infra.factory import build_infra

        with tempfile.TemporaryDirectory() as tmp:
            tb = Path(tmp)
            with pytest.raises(ValueError, match="Unsupported blob backend"):
                await build_infra(
                    {
                        "relational": {"backend": "sqlite", "path": str(tb / "test.db")},
                        "vector": {"backend": "sqlite-vec", "dim": 4},
                        "keyword": {"backend": "fts5"},
                        "blob": {"backend": "s3"},
                        "cache": {"backend": "memory", "max_size": 10},
                    },
                    run_migrations_flag=False,
                )

    async def test_jobs_optional(self):
        import tempfile
        from pathlib import Path

        from src.infra.factory import build_infra

        with tempfile.TemporaryDirectory() as tmp:
            tb = Path(tmp)
            infra = await build_infra(
                {
                    "relational": {"backend": "sqlite", "path": str(tb / "test.db")},
                    "vector": {"backend": "sqlite-vec", "dim": 4},
                    "keyword": {"backend": "fts5"},
                    "blob": {"backend": "filesystem", "root": str(tb / "blobs")},
                    "cache": {"backend": "memory", "max_size": 50},
                    "eventbus": {"backend": "inproc", "persist": False},
                },
                run_migrations_flag=False,
            )
            assert infra.jobs is None
            await infra.eventbus.shutdown()
            await infra.relational.close()
