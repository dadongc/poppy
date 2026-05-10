from __future__ import annotations

from src.common.types import VectorHit, VectorItem
from src.infra.relational.sqlite import SqliteStore


class SqliteVecIndex:
    """Placeholder vector index backed by SQLite (brute-force cosine)."""

    def __init__(self, store: SqliteStore, dim: int) -> None:
        self._store = store
        self._dim = dim
        self._init = False

    async def init(self) -> None:
        await self._store.execute("""
            CREATE TABLE IF NOT EXISTS _vec_index (
                id TEXT NOT NULL,
                namespace TEXT NOT NULL,
                user_id TEXT NOT NULL,
                vector TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                PRIMARY KEY (namespace, id)
            )
        """)
        await self._store.execute(
            "CREATE INDEX IF NOT EXISTS _vec_ns_user ON _vec_index(namespace, user_id)"
        )
        self._init = True

    async def upsert(self, namespace: str, items: list[VectorItem]) -> None:
        import json

        for it in items:
            vec_json = json.dumps(it.vector)
            meta_json = json.dumps(it.metadata)
            await self._store.execute(
                """INSERT INTO _vec_index(id, namespace, user_id, vector, metadata)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(namespace, id) DO UPDATE SET
                   vector = excluded.vector, metadata = excluded.metadata""",
                it.id,
                namespace,
                it.user_id,
                vec_json,
                meta_json,
            )

    async def search(
        self,
        namespace: str,
        query_vec: list[float],
        top_k: int,
        user_id: str,
        filter: dict | None = None,
    ) -> list[VectorHit]:
        import json

        rows = await self._store.fetch_all(
            "SELECT id, vector, metadata FROM _vec_index WHERE namespace = ? AND user_id = ?",
            namespace,
            user_id,
        )

        scored = []
        for r in rows:
            vec = json.loads(r["vector"])
            score = self._cosine(query_vec, vec)
            meta = json.loads(r["metadata"])
            if filter and not self._match_filter(meta, filter):
                continue
            scored.append(VectorHit(id=r["id"], score=score, metadata=meta))

        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

    async def delete(self, namespace: str, ids: list[str]) -> None:
        for id_ in ids:
            await self._store.execute(
                "DELETE FROM _vec_index WHERE namespace = ? AND id = ?", namespace, id_
            )

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _match_filter(metadata: dict, filter: dict) -> bool:
        for k, v in filter.items():
            if metadata.get(k) != v:
                return False
        return True
