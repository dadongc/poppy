from __future__ import annotations

import pytest


class TestSqliteStoreBasic:
    async def test_init_creates_db(self, sqlite_store):
        assert sqlite_store._conn is not None

    async def test_execute_insert(self, sqlite_store):
        await sqlite_store.execute("CREATE TABLE test_basic (id INTEGER PRIMARY KEY, name TEXT)")
        rows = await sqlite_store.execute("INSERT INTO test_basic VALUES (?, ?)", 1, "hello")
        assert rows == 1

    async def test_fetch_one(self, sqlite_store):
        await sqlite_store.execute("CREATE TABLE test_fetch (id INTEGER PRIMARY KEY, name TEXT)")
        await sqlite_store.execute("INSERT INTO test_fetch VALUES (?, ?)", 1, "alice")
        row = await sqlite_store.fetch_one("SELECT * FROM test_fetch WHERE id = ?", 1)
        assert row is not None
        assert row["name"] == "alice"

    async def test_fetch_one_missing(self, sqlite_store):
        row = await sqlite_store.fetch_one("SELECT 1 WHERE 1 = 0")
        assert row is None

    async def test_fetch_all(self, sqlite_store):
        await sqlite_store.execute("CREATE TABLE test_fa (id INTEGER PRIMARY KEY, x INTEGER)")
        await sqlite_store.execute("INSERT INTO test_fa VALUES (?, ?)", 1, 10)
        await sqlite_store.execute("INSERT INTO test_fa VALUES (?, ?)", 2, 20)
        rows = await sqlite_store.fetch_all("SELECT * FROM test_fa ORDER BY id")
        assert len(rows) == 2


class TestSqliteTransaction:
    async def test_commit(self, sqlite_store):
        await sqlite_store.execute("CREATE TABLE test_tx (id INTEGER PRIMARY KEY, val TEXT)")
        async with sqlite_store.transaction() as tx:
            await tx.execute("INSERT INTO test_tx VALUES (?, ?)", 1, "a")
        row = await sqlite_store.fetch_one("SELECT * FROM test_tx WHERE id = ?", 1)
        assert row["val"] == "a"

    async def test_rollback(self, sqlite_store):
        await sqlite_store.execute("CREATE TABLE test_rb (id INTEGER PRIMARY KEY, val TEXT)")
        with pytest.raises(ValueError):
            async with sqlite_store.transaction() as tx:
                await tx.execute("INSERT INTO test_rb VALUES (?, ?)", 1, "a")
                raise ValueError("boom")
        row = await sqlite_store.fetch_one("SELECT * FROM test_rb WHERE id = ?", 1)
        assert row is None

    async def test_fetch_in_transaction(self, sqlite_store):
        await sqlite_store.execute("CREATE TABLE test_tx_fetch (id INTEGER PRIMARY KEY, val TEXT)")
        await sqlite_store.execute("INSERT INTO test_tx_fetch VALUES (?, ?)", 1, "x")
        async with sqlite_store.transaction() as tx:
            row = await tx.fetch_one("SELECT * FROM test_tx_fetch WHERE id = ?", 1)
            assert row["val"] == "x"


class TestSqliteMigrator:
    async def test_run_migrations(self, sqlite_store):
        import tempfile
        from pathlib import Path

        from src.infra.relational.migrator import run_migrations

        with tempfile.TemporaryDirectory() as tmp:
            m1 = Path(tmp) / "001_init.sql"
            m1.write_text(
                "CREATE TABLE _schema_meta ("
                " version INTEGER PRIMARY KEY,"
                " applied_at TEXT DEFAULT (datetime('now')),"
                " description TEXT"
                ");"
                "CREATE TABLE mig_test (id INTEGER PRIMARY KEY, name TEXT);"
                "INSERT INTO _schema_meta(version, description) VALUES (1, '001_init');"
            )
            m2 = Path(tmp) / "002_add_col.sql"
            m2.write_text(
                "ALTER TABLE mig_test ADD COLUMN extra TEXT;"
                "INSERT INTO _schema_meta(version, description) VALUES (2, '002_add_col');"
            )

            await run_migrations(sqlite_store, Path(tmp))

            meta = await sqlite_store.fetch_all("SELECT * FROM _schema_meta ORDER BY version")
            assert len(meta) == 2
            assert meta[0]["version"] == 1
            assert meta[1]["version"] == 2

            await run_migrations(sqlite_store, Path(tmp))
            meta2 = await sqlite_store.fetch_all("SELECT * FROM _schema_meta ORDER BY version")
            assert len(meta2) == 2
