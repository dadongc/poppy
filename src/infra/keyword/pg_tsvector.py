from __future__ import annotations

from src.common.types import KeywordHit


class PgTsvectorIndex:
    """Keyword index backed by PostgreSQL tsvector with zhparser."""

    def __init__(self, store, ts_config: str = "zhcfg") -> None:
        self._store = store
        self._ts_config = ts_config
        self._tables: set[str] = set()

    async def init(self) -> None:
        pass

    async def _ensure_table(self, namespace: str) -> None:
        if namespace in self._tables:
            return
        table = f"kw_{namespace}"
        await self._store.execute(
            f"""CREATE TABLE IF NOT EXISTS {table} (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                text TEXT NOT NULL,
                tsv tsvector GENERATED ALWAYS AS
                    (to_tsvector('{self._ts_config}', text)) STORED,
                metadata JSONB DEFAULT '{{}}'
            )"""
        )
        await self._store.execute(
            f"CREATE INDEX IF NOT EXISTS {table}_user_idx ON {table}(user_id)"
        )
        await self._store.execute(
            f"CREATE INDEX IF NOT EXISTS {table}_tsv_idx ON {table} USING GIN(tsv)"
        )
        self._tables.add(namespace)

    async def index(
        self,
        namespace: str,
        doc_id: str,
        text: str,
        user_id: str,
        metadata: dict | None = None,
    ) -> None:
        await self._ensure_table(namespace)
        table = f"kw_{namespace}"
        await self._store.execute(f"DELETE FROM {table} WHERE id = $1", doc_id)
        await self._store.execute(
            f"""INSERT INTO {table}(id, user_id, text, metadata)
                VALUES ($1, $2, $3, $4)""",
            doc_id,
            user_id,
            text,
            metadata or {},
        )

    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int,
        user_id: str,
        filter: dict | None = None,
    ) -> list[KeywordHit]:
        await self._ensure_table(namespace)
        table = f"kw_{namespace}"
        rows = await self._store.fetch_all(
            f"""SELECT id, metadata,
                       ts_rank(tsv, plainto_tsquery('{self._ts_config}', $1)) AS score,
                       ts_headline('{self._ts_config}', text,
                                   plainto_tsquery('{self._ts_config}', $1),
                                   'MaxWords=20, MinWords=10') AS snippet
                FROM {table}
                WHERE user_id = $2
                  AND tsv @@ plainto_tsquery('{self._ts_config}', $1)
                ORDER BY score DESC
                LIMIT $3""",
            query,
            user_id,
            top_k,
        )
        hits = []
        for r in rows:
            if filter and not _match_filter(r["metadata"], filter):
                continue
            hits.append(
                KeywordHit(
                    id=r["id"],
                    score=r["score"],
                    snippet=r["snippet"] or "",
                    metadata=r["metadata"],
                )
            )
        return hits

    async def delete(self, namespace: str, doc_ids: list[str]) -> None:
        await self._ensure_table(namespace)
        table = f"kw_{namespace}"
        for doc_id in doc_ids:
            await self._store.execute(f"DELETE FROM {table} WHERE id = $1", doc_id)


def _match_filter(metadata: dict, filter: dict) -> bool:
    for k, v in filter.items():
        if metadata.get(k) != v:
            return False
    return True
