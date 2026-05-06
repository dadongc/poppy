from __future__ import annotations

from src.common.types import VectorHit, VectorItem


class PgVectorIndex:
    """Vector index backed by pgvector with HNSW support."""

    def __init__(self, store, dim: int, metric: str = "cosine") -> None:
        self._store = store
        self._dim = dim
        self._metric = metric
        self._tables: set[str] = set()

    async def init(self) -> None:
        pass  # tables are created on demand per namespace

    async def _ensure_table(self, namespace: str) -> None:
        if namespace in self._tables:
            return
        table = f"vec_{namespace}"
        ops = {
            "cosine": "vector_cosine_ops",
            "l2": "vector_l2_ops",
            "inner_product": "vector_ip_ops",
        }[self._metric]
        await self._store.execute(
            f"""CREATE TABLE IF NOT EXISTS {table} (
                id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                embedding vector({self._dim}) NOT NULL,
                metadata JSONB DEFAULT '{{}}',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (id)
            )"""
        )
        await self._store.execute(
            f"CREATE INDEX IF NOT EXISTS {table}_user_idx ON {table}(user_id)"
        )
        await self._store.execute(
            f"""CREATE INDEX IF NOT EXISTS {table}_hnsw_idx ON {table}
                USING hnsw (embedding {ops}) WITH (m=16, ef_construction=64)"""
        )
        self._tables.add(namespace)

    async def upsert(self, namespace: str, items: list[VectorItem]) -> None:
        await self._ensure_table(namespace)
        table = f"vec_{namespace}"
        for it in items:
            await self._store.execute(
                f"""INSERT INTO {table}(id, user_id, embedding, metadata)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (id) DO UPDATE
                    SET embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata""",
                it.id,
                it.user_id,
                _vec(it.vector),
                it.metadata,
            )

    async def search(
        self,
        namespace: str,
        query_vec: list[float],
        top_k: int,
        user_id: str,
        filter: dict | None = None,
    ) -> list[VectorHit]:
        await self._ensure_table(namespace)
        table = f"vec_{namespace}"
        rows = await self._store.fetch_all(
            f"""SELECT id, metadata,
                       1 - (embedding <=> $1) AS score
                FROM {table}
                WHERE user_id = $2
                ORDER BY embedding <=> $1
                LIMIT $3""",
            _vec(query_vec),
            user_id,
            top_k,
        )
        hits = []
        for r in rows:
            if filter and not _match_filter(r["metadata"], filter):
                continue
            hits.append(VectorHit(id=r["id"], score=r["score"], metadata=r["metadata"]))
        return hits

    async def delete(self, namespace: str, ids: list[str]) -> None:
        await self._ensure_table(namespace)
        table = f"vec_{namespace}"
        for id_ in ids:
            await self._store.execute(f"DELETE FROM {table} WHERE id = $1", id_)


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def _match_filter(metadata: dict, filter: dict) -> bool:
    for k, v in filter.items():
        if metadata.get(k) != v:
            return False
    return True
