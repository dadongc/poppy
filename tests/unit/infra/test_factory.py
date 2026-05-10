from __future__ import annotations

import tempfile
from pathlib import Path

from src.infra.factory import build_infra


class TestBuildInfra:
    async def test_build_dev_infra(self):
        with tempfile.TemporaryDirectory() as tmp:
            tb = Path(tmp)

            config = {
                "relational": {"backend": "sqlite", "path": str(tb / "test.db")},
                "vector": {"backend": "sqlite-vec", "dim": 4},
                "keyword": {"backend": "fts5"},
                "blob": {"backend": "filesystem", "root": str(tb / "blobs")},
                "cache": {"backend": "memory", "max_size": 50},
                "eventbus": {"backend": "inproc", "persist": False},
            }

            infra = await build_infra(config, run_migrations_flag=False)

            assert infra.relational is not None
            assert infra.vector is not None
            assert infra.keyword is not None
            assert infra.blob is not None
            assert infra.cache is not None
            assert infra.eventbus is not None

            # Test full round-trip through all layers
            await infra.relational.execute(
                "CREATE TABLE IF NOT EXISTS smoke (id INTEGER PRIMARY KEY, val TEXT)"
            )
            await infra.relational.execute("INSERT INTO smoke VALUES (?, ?)", 1, "hello")
            row = await infra.relational.fetch_one("SELECT val FROM smoke WHERE id = ?", 1)
            assert row is not None
            assert row["val"] == "hello"

            await infra.cache.set("k", "v")
            assert await infra.cache.get("k") == "v"

            await infra.blob.put("factory_test.txt", b"factory")
            assert await infra.blob.exists("factory_test.txt")

            from src.common.types import VectorItem

            await infra.vector.upsert(
                "test", [VectorItem(id="a", vector=[1.0, 0.0, 0.0, 0.0], user_id="u1")]
            )
            hits = await infra.vector.search("test", [1.0, 0.0, 0.0, 0.0], top_k=1, user_id="u1")
            assert hits[0].id == "a"

            await infra.eventbus.shutdown()
            await infra.relational.close()
