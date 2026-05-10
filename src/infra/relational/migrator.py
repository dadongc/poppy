from __future__ import annotations

from pathlib import Path
from typing import Any


def _split_sql(sql: str) -> list[str]:
    """Split multi-statement SQL, ignoring empty strings and pure-whitespace."""
    return [s.strip() for s in sql.split(";") if s.strip()]


async def run_migrations(store: Any, migrations_dir: Path) -> None:
    """Execute SQL migration files in order, tracking applied versions in _schema_meta."""

    if not migrations_dir.exists():
        return

    # Query already-applied versions; if _schema_meta doesn't exist yet, run all.
    try:
        applied_rows = await store.fetch_all("SELECT version FROM _schema_meta")
        applied_versions = {r["version"] for r in applied_rows}
    except Exception:
        applied_versions = set()

    for f in sorted(migrations_dir.glob("*.sql")):
        version = int(f.name.split("_")[0])
        if version in applied_versions:
            continue
        sql = f.read_text()
        async with store.transaction() as tx:
            for stmt in _split_sql(sql):
                if stmt:
                    await tx.execute(stmt)
