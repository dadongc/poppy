"""Smoke test for production infrastructure connectivity.

Usage:
    python -m src.infra.smoke_test

Requires: .env with PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD set.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader — one key=value per line, ignores comments and blanks."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key not in os.environ:
            os.environ[key] = val


async def test_postgres() -> bool:
    from src.common.config import load_config

    cfg = load_config("config/prod.yaml")
    rel_cfg = cfg.infra.relational

    print(f"  host: {rel_cfg['host']}:{rel_cfg['port']}, db={rel_cfg['database']}, user={rel_cfg['user']}")

    from src.infra.relational.postgres import PostgresStore

    store = PostgresStore(
        host=rel_cfg["host"],
        port=rel_cfg["port"],
        database=rel_cfg["database"],
        user=rel_cfg["user"],
        password=rel_cfg["password"],
    )
    await store.init()
    print("  [OK] connected + pool created")

    # Basic query
    row = await store.fetch_one("SELECT 1 AS val")
    assert row is not None and row["val"] == 1
    print("  [OK] SELECT 1")

    # Execute DDL
    await store.execute("""
        CREATE TABLE IF NOT EXISTS _smoke_test (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    print("  [OK] CREATE TABLE _smoke_test")

    # INSERT
    rows = await store.execute("INSERT INTO _smoke_test(name) VALUES ($1)", "hello")
    assert rows == 1
    print("  [OK] INSERT")

    # SELECT
    row = await store.fetch_one("SELECT name FROM _smoke_test WHERE name = $1", "hello")
    assert row is not None and row["name"] == "hello"
    print("  [OK] SELECT")

    # UPDATE
    await store.execute("UPDATE _smoke_test SET name = $1 WHERE name = $2", "world", "hello")
    row = await store.fetch_one("SELECT name FROM _smoke_test WHERE name = $1", "world")
    assert row is not None and row["name"] == "world"
    print("  [OK] UPDATE")

    # DELETE
    await store.execute("DELETE FROM _smoke_test WHERE name = $1", "world")
    row = await store.fetch_one("SELECT 1 FROM _smoke_test")
    assert row is None
    print("  [OK] DELETE")

    # DROP
    await store.execute("DROP TABLE IF EXISTS _smoke_test")
    print("  [OK] DROP TABLE")

    # LISTEN/NOTIFY
    ready = asyncio.Event()
    received: list[str] = []

    async def listener() -> None:
        async for payload in store.listen("smoke_channel"):
            received.append(payload)
            if len(received) >= 1:
                break
        ready.set()

    task = asyncio.create_task(listener())
    await asyncio.sleep(0.3)
    await store.notify("smoke_channel", "ping")
    await asyncio.wait_for(task, timeout=3.0)

    assert "ping" in received, f"Expected 'ping', got {received}"
    print("  [OK] LISTEN/NOTIFY")

    # Transaction
    async with store.transaction() as tx:
        await tx.execute("CREATE TABLE IF NOT EXISTS _smoke_tx (id SERIAL PRIMARY KEY, val TEXT)")
        await tx.execute("INSERT INTO _smoke_tx(val) VALUES ($1)", "tx_test")
    row = await store.fetch_one("SELECT val FROM _smoke_tx WHERE val = $1", "tx_test")
    assert row is not None and row["val"] == "tx_test"
    await store.execute("DROP TABLE IF EXISTS _smoke_tx")
    print("  [OK] Transaction (commit)")

    # Transaction rollback
    try:
        async with store.transaction() as tx:
            await tx.execute("CREATE TABLE IF NOT EXISTS _smoke_rb (id SERIAL PRIMARY KEY, val TEXT)")
            await tx.execute("INSERT INTO _smoke_rb(val) VALUES ($1)", "rb_test")
            raise ValueError("intentional rollback")
    except ValueError:
        pass
    # Verify rollback: table should not exist
    row = await store.fetch_one(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = '_smoke_rb') AS e"
    )
    print("  [OK] Transaction (rollback)")

    await store.close()
    print("  [OK] Close")
    return True


async def test_pgvector(store) -> bool:
    from src.common.types import VectorItem
    from src.infra.vector.pgvector import PgVectorIndex

    # Ensure extension is installed
    try:
        await store.execute("CREATE EXTENSION IF NOT EXISTS vector")
        print("  [OK] CREATE EXTENSION vector")
    except Exception as e:
        print(f"  [SKIP] vector extension not available: {e}")
        return False

    idx = PgVectorIndex(store, dim=4)
    await idx.init()

    await idx._ensure_table("smoke_vec")
    print("  [OK] create vec_smoke_vec table + indexes")

    await idx.upsert("smoke_vec", [
        VectorItem(id="a", vector=[1.0, 0.0, 0.0, 0.0], user_id="u1"),
        VectorItem(id="b", vector=[0.0, 1.0, 0.0, 0.0], user_id="u1"),
    ])
    print("  [OK] upsert 2 vectors")

    hits = await idx.search("smoke_vec", [1.0, 0.0, 0.0, 0.0], top_k=2, user_id="u1")
    assert len(hits) == 2
    assert hits[0].id == "a"
    assert hits[0].score > 0.9
    print(f"  [OK] search: top hit={hits[0].id}, score={hits[0].score:.4f}")

    await idx.delete("smoke_vec", ["a", "b"])
    hits = await idx.search("smoke_vec", [1.0, 0.0, 0.0, 0.0], top_k=1, user_id="u1")
    assert len(hits) == 0
    print("  [OK] delete + verify")

    # Clean up
    await store.execute("DROP TABLE IF EXISTS vec_smoke_vec")
    return True


async def test_tsvector(store) -> bool:
    from src.infra.keyword.pg_tsvector import PgTsvectorIndex

    idx = PgTsvectorIndex(store, ts_config="simple")
    await idx.init()

    await idx._ensure_table("smoke_kw")
    print("  [OK] create kw_smoke_kw table + indexes")

    await idx.index("smoke_kw", "d1", "hello world", "u1")
    await idx.index("smoke_kw", "d2", "hello poppy", "u1")
    print("  [OK] index 2 docs")

    hits = await idx.search("smoke_kw", "hello", top_k=5, user_id="u1")
    assert len(hits) >= 1
    assert "hello" in hits[0].snippet.lower()
    print(f"  [OK] search: {len(hits)} hits, snippet='{hits[0].snippet}'")

    await idx.delete("smoke_kw", ["d1", "d2"])
    hits = await idx.search("smoke_kw", "hello", top_k=1, user_id="u1")
    assert len(hits) == 0
    print("  [OK] delete + verify")

    await store.execute("DROP TABLE IF EXISTS kw_smoke_kw")
    return True


async def test_job_queue(store) -> bool:
    from src.infra.jobs.pg_jobs import PgJobQueue

    # Create table first
    await store.execute("""
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
        )
    """)

    jq = PgJobQueue(store)
    await jq.init()

    jid = await jq.enqueue("scan", {"url": "test"}, priority=5)
    assert jid.startswith("job_")
    print(f"  [OK] enqueue: {jid}")

    job = await jq.claim_next("worker1", lease_sec=60)
    assert job is not None and job.job_id == jid
    print(f"  [OK] claim_next: {job.job_id}")

    await jq.mark_done(jid)
    # Verify done
    job2 = await jq.claim_next("worker1")
    assert job2 is None  # no pending jobs
    print("  [OK] mark_done + verify no pending")

    # Test retry
    jid2 = await jq.enqueue("fetch", {"url": "retry_test"})
    await jq.mark_failed(jid2, "timeout", retry=True)
    # After retry, job should be back to pending with retry_count=1
    rows = await store.fetch_all("SELECT * FROM async_jobs WHERE job_id = $1", jid2)
    assert len(rows) == 1
    job_data = rows[0]
    assert job_data["state"] == "pending"
    assert job_data["retry_count"] == 1
    print("  [OK] mark_failed + retry -> pending")

    # Test max retries
    await store.execute(
        "UPDATE async_jobs SET retry_count = 2, state = 'pending' WHERE job_id = $1", jid2
    )
    await jq.mark_failed(jid2, "timeout again", retry=True)
    rows = await store.fetch_all("SELECT state FROM async_jobs WHERE job_id = $1", jid2)
    assert rows[0]["state"] == "failed"
    print("  [OK] max_retries exceeded -> failed")

    await store.execute("DROP TABLE IF EXISTS async_jobs")
    return True


async def main() -> int:
    print("=== Poppy Infra Smoke Test ===\n")
    load_dotenv()

    # 1. PostgreSQL
    print("[1/4] PostgreSQL connection ...")
    try:
        await test_postgres()
    except Exception as e:
        print(f"  [FAIL] {e}")
        return 1

    # Re-connect for vector/keyword/jobs tests
    from src.common.config import load_config

    cfg = load_config("config/prod.yaml")
    rel_cfg = cfg.infra.relational
    from src.infra.relational.postgres import PostgresStore

    store = PostgresStore(
        host=rel_cfg["host"],
        port=rel_cfg["port"],
        database=rel_cfg["database"],
        user=rel_cfg["user"],
        password=rel_cfg["password"],
    )
    await store.init()

    # 2. pgvector
    print("\n[2/4] pgvector ...")
    try:
        await test_pgvector(store)
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 3. tsvector
    print("\n[3/4] tsvector ...")
    try:
        await test_tsvector(store)
    except Exception as e:
        print(f"  [FAIL] {e}")

    # 4. JobQueue
    print("\n[4/4] PgJobQueue ...")
    try:
        await test_job_queue(store)
    except Exception as e:
        print(f"  [FAIL] {e}")

    await store.close()

    print("\n=== Smoke test complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
