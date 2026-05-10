from __future__ import annotations

from src.common.types import KeywordHit
from src.infra.relational.sqlite import SqliteStore


class Fts5Index:
    """Placeholder keyword index backed by SQLite FTS5."""

    def __init__(self, store: SqliteStore) -> None:
        self._store = store
        self._init = False

    async def init(self) -> None:
        await self._store.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS _fts_index USING fts5(
                namespace, doc_id, user_id, text, metadata,
                tokenize='unicode61'
            )
        """)
        self._init = True

    async def index(
        self,
        namespace: str,
        doc_id: str,
        text: str,
        user_id: str,
        metadata: dict | None = None,
    ) -> None:
        import json

        meta_json = json.dumps(metadata or {})
        # Delete existing then insert
        await self._store.execute(
            "DELETE FROM _fts_index WHERE namespace = ? AND doc_id = ?", namespace, doc_id
        )
        await self._store.execute(
            "INSERT INTO _fts_index(namespace, doc_id, user_id, text, metadata) VALUES (?, ?, ?, ?, ?)",
            namespace,
            doc_id,
            user_id,
            text,
            meta_json,
        )

    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int,
        user_id: str,
        filter: dict | None = None,
    ) -> list[KeywordHit]:
        import json

        # FTS5 simple query: escape special chars
        safe_query = query.replace('"', '""')
        rows = await self._store.fetch_all(
            """SELECT doc_id, text, metadata,
                      rank AS score,
                      snippet(_fts_index, 1, '<b>', '</b>', '...', 32) AS snippet
               FROM _fts_index
               WHERE namespace = ? AND user_id = ? AND _fts_index MATCH ?
               ORDER BY rank
               LIMIT ?""",
            namespace,
            user_id,
            f'"{safe_query}"',
            top_k,
        )
        hits = []
        for r in rows:
            meta = json.loads(r["metadata"])
            if filter and not self._match_filter(meta, filter):
                continue
            # FTS5 rank is negative; normalize
            hits.append(
                KeywordHit(
                    id=r["doc_id"],
                    score=abs(r["score"]) / 10.0 if r["score"] else 0.0,
                    snippet=r["snippet"] or "",
                    metadata=meta,
                )
            )
        return hits

    async def delete(self, namespace: str, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            await self._store.execute(
                "DELETE FROM _fts_index WHERE namespace = ? AND doc_id = ?", namespace, doc_id
            )

    @staticmethod
    def _match_filter(metadata: dict, filter: dict) -> bool:
        for k, v in filter.items():
            if metadata.get(k) != v:
                return False
        return True
