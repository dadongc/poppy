from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from src.common.types import KeywordHit, RetrievalHit, RetrievalQuery, VectorHit
from src.infra.protocols import KeywordIndex, RelationalStore, VectorIndex
from src.service.embedding.gateway import EmbeddingGateway
from src.service.memory.service import MemoryService


@dataclass(slots=True)
class _FusedHit:
    id: str
    score: float = 0.0


class Retriever:
    def __init__(
        self,
        *,
        memory: MemoryService,
        kb_vector: VectorIndex,
        kb_keyword: KeywordIndex,
        embedding: EmbeddingGateway,
        store: RelationalStore,
    ) -> None:
        self._memory = memory
        self._kb_vector = kb_vector
        self._kb_keyword = kb_keyword
        self._embedding = embedding
        self._store = store

    async def search(self, q: RetrievalQuery) -> list[RetrievalHit]:
        async_tasks = []
        if "memory" in q.channels:
            async_tasks.append(self._search_memory(q))
        if "kb" in q.channels:
            async_tasks.append(self._search_kb(q))

        all_hits: list[RetrievalHit] = []
        results = await asyncio.gather(*async_tasks)
        for hits in results:
            all_hits.extend(hits)

        all_hits.sort(key=lambda h: -h.score)
        all_hits = all_hits[: q.top_k * 2]

        if q.diversify and len(all_hits) > q.top_k:
            qvec = await self._embedding.embed_one(q.text)
            all_hits = self._mmr_select(all_hits, qvec, q.top_k)
        else:
            all_hits = all_hits[: q.top_k]
        return all_hits

    async def _search_memory(self, q: RetrievalQuery) -> list[RetrievalHit]:
        records = await self._memory.recall(
            q.user_id, q.text, top_k=q.top_k, diversify=False,
        )
        return [
            RetrievalHit(
                channel="memory",
                chunk_id=r.memory_id,
                text=r.content,
                score=getattr(r, "score", 1.0),
            )
            for r in records
        ]

    async def _search_kb(self, q: RetrievalQuery) -> list[RetrievalHit]:
        qvec = await self._embedding.embed_one(q.text)
        vec_hits, kw_hits = await asyncio.gather(
            self._kb_vector.search("kb_chunks", qvec, q.top_k * 3, q.user_id),
            self._kb_keyword.search("kb_chunks", q.text, q.top_k * 3, q.user_id),
        )

        fused = self._fuse_hits(vec_hits, kw_hits)

        result: list[RetrievalHit] = []
        for h in fused[: q.top_k * 2]:
            row = await self._store.fetch_one(
                "SELECT * FROM kb_chunks WHERE chunk_id = ? AND user_id = ?",
                h.id,
                q.user_id,
            )
            if row is None:
                continue
            heading_path_raw = row.get("heading_path", "[]")
            import json

            heading_path = (
                json.loads(heading_path_raw) if isinstance(heading_path_raw, str)
                else heading_path_raw if isinstance(heading_path_raw, list)
                else []
            )
            result.append(
                RetrievalHit(
                    channel="kb",
                    chunk_id=row["chunk_id"],
                    doc_id=row["doc_id"],
                    text=row["text"],
                    score=h.score,
                    citation={
                        "doc_id": row["doc_id"],
                        "heading_path": heading_path,
                        "char_start": row.get("char_start", 0),
                        "char_end": row.get("char_end", 0),
                    },
                )
            )
        return result

    @staticmethod
    def _fuse_hits(
        vec_hits: list[VectorHit],
        kw_hits: list[KeywordHit],
        vec_w: float = 0.7,
        kw_w: float = 0.3,
    ) -> list[_FusedHit]:
        def _norm(hits: Sequence[VectorHit | KeywordHit]) -> dict[str, float]:
            if not hits:
                return {}
            scores = [h.score for h in hits]
            mn, mx = min(scores), max(scores)
            if mx == mn:
                return {h.id: 1.0 for h in hits}
            return {h.id: (h.score - mn) / (mx - mn) for h in hits}

        vec_norm = _norm(vec_hits)
        kw_norm = _norm(kw_hits)

        fused: dict[str, _FusedHit] = {}
        for h in vec_hits:
            fused[h.id] = _FusedHit(id=h.id, score=vec_w * vec_norm.get(h.id, 0.0))
        for kh in kw_hits:
            s = kw_w * kw_norm.get(kh.id, 0.0)
            if kh.id in fused:
                fused[kh.id].score += s
            else:
                fused[kh.id] = _FusedHit(id=kh.id, score=s)

        result = list(fused.values())
        result.sort(key=lambda h: -h.score)
        return result

    @staticmethod
    def _mmr_select(
        hits: list[RetrievalHit],
        query_vec: list[float],
        top_k: int,
        lambda_: float = 0.5,
    ) -> list[RetrievalHit]:
        selected: list[RetrievalHit] = []
        remaining = list(range(len(hits)))

        while remaining and len(selected) < top_k:
            best_idx = -1
            best_score = -float("inf")
            for i in remaining:
                relevance = hits[i].score
                diversity = 0.0
                if selected:
                    max_sim = max(
                        1.0
                        - abs(hits[i].score - s.score)
                        / max(abs(hits[i].score - s.score), 0.001)
                        for s in selected
                    )
                    diversity = 1.0 - max_sim
                mmr = lambda_ * relevance + (1 - lambda_) * diversity
                if mmr > best_score:
                    best_score = mmr
                    best_idx = i
            if best_idx >= 0:
                selected.append(hits[best_idx])
                remaining.remove(best_idx)
            else:
                break
        return selected
