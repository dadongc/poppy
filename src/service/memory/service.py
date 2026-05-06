from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from src.common.clock import now_ts
from src.common.ids import new_id
from src.common.types import (
    Event,
    EventType,
    KeywordHit,
    MemoryKind,
    MemoryRecord,
    VectorHit,
    VectorItem,
)
from src.infra.protocols import (
    EventBus,
    JobQueue,
    KeywordIndex,
    RelationalStore,
    VectorIndex,
)
from src.service.embedding.gateway import EmbeddingGateway
from src.service.llm_protocol import LLMService
from src.service.memory.extractor import MemoryExtractor


@dataclass(slots=True)
class _FusedHit:
    memory_id: str
    score: float
    metadata: dict = field(default_factory=dict)


class MemoryService:
    def __init__(
        self,
        *,
        store: RelationalStore,
        vector: VectorIndex,
        keyword: KeywordIndex,
        embedding: EmbeddingGateway,
        jobs: JobQueue | None,
        event_bus: EventBus,
        llm: LLMService,
        extractor: MemoryExtractor | None = None,
    ) -> None:
        self._store = store
        self._vector = vector
        self._keyword = keyword
        self._embedding = embedding
        self._jobs = jobs
        self._event_bus = event_bus
        self._llm = llm
        self._extractor = extractor

    # ------------------------------------------------------------------
    # remember
    # ------------------------------------------------------------------

    async def remember(
        self,
        *,
        user_id: str,
        kind: str,
        content: str,
        source_type: str = "explicit",
        importance: float = 0.5,
        confidence: float = 1.0,
        source_run_id: str | None = None,
        source_session_id: str | None = None,
        occurred_at: float | None = None,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> MemoryRecord:
        vec = await self._embedding.embed_one(content)

        # dedup via vector similarity
        similar = await self._vector.search(
            "memory", vec, top_k=3, user_id=user_id, filter={"kind": kind}
        )
        for hit in similar:
            if hit.score > 0.92:
                existing = await self._get_record(hit.id, user_id)
                if existing is not None:
                    return await self._merge(existing, content, importance)

        # conflict detection for fact-type memories
        if kind in (MemoryKind.PROFILE, MemoryKind.PREFERENCE, MemoryKind.FACT):
            await self._check_conflicts(user_id, kind, content, similar)

        memory_id = new_id("mem")
        ts = now_ts()
        async with self._store.transaction() as tx:
            await tx.execute(
                """INSERT INTO memory_records(memory_id, user_id, kind, content,
                   source_type, source_run_id, source_session_id,
                   created_at, updated_at, occurred_at,
                   confidence, importance, tags, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                memory_id,
                user_id,
                kind,
                content,
                source_type,
                source_run_id or "",
                source_session_id or "",
                ts,
                ts,
                occurred_at,
                confidence,
                importance,
                json.dumps(tags or [], ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
            )

        # vector/keyword index outside transaction to avoid SQLite lock deadlock
        await self._vector.upsert(
            "memory",
            [
                VectorItem(
                    id=memory_id,
                    vector=vec,
                    user_id=user_id,
                    metadata={"kind": kind},
                )
            ],
        )
        await self._keyword.index("memory", memory_id, content, user_id, metadata={"kind": kind})

        await self._event_bus.publish(
            Event(
                event_id=new_id("evt"),
                type=EventType.MEMORY_WRITTEN,
                run_id=source_run_id or "",
                user_id=user_id,
                ts=ts,
                payload={"memory_id": memory_id, "kind": kind},
            )
        )
        record = await self._get_record(memory_id, user_id)
        assert record is not None  # just inserted
        return record

    # ------------------------------------------------------------------
    # forget
    # ------------------------------------------------------------------

    async def forget(self, memory_id: str, user_id: str) -> None:
        await self._store.execute(
            "UPDATE memory_records SET state = 'deleted', updated_at = ? WHERE memory_id = ? AND user_id = ?",
            now_ts(),
            memory_id,
            user_id,
        )
        try:
            await self._vector.delete("memory", [memory_id])
            await self._keyword.delete("memory", [memory_id])
        except Exception:
            pass

    # ------------------------------------------------------------------
    # recall
    # ------------------------------------------------------------------

    async def recall(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int = 10,
        kinds: list[str] | None = None,
        diversify: bool = True,
    ) -> list[MemoryRecord]:
        vec = await self._embedding.embed_one(query)

        vec_hits, kw_hits = await self._vector.search(
            "memory", vec, top_k * 3, user_id
        ), await self._keyword.search("memory", query, top_k * 3, user_id)

        # normalize and fuse scores
        fused = self._fuse_hits(vec_hits, kw_hits, vec_weight=0.7, kw_weight=0.3)

        # fetch records and apply recency + importance boost
        records: list[MemoryRecord] = []
        filtered: list[_FusedHit] = []
        for h in fused:
            r = await self._get_record(h.memory_id, user_id)
            if r is None or r.state != "active":
                continue
            if kinds and r.kind not in kinds:
                continue
            records.append(r)
            h.score = self._apply_boost(h.score, r)
            filtered.append(h)

        # MMR diversification
        if diversify and len(filtered) > top_k:
            selected_idxs = self._mmr_select(filtered, vec, top_k, lambda_=0.5)
            records = [records[i] for i in selected_idxs]
        else:
            filtered.sort(key=lambda h: -h.score)
            records = [records[i] for i in range(min(top_k, len(records)))]

        # update recall stats
        if records:
            ids = [r.memory_id for r in records]
            await self._store.execute(
                """UPDATE memory_records SET last_recalled_at = ?, recall_count = recall_count + 1
                   WHERE memory_id IN (SELECT value FROM json_each(?))""",
                now_ts(),
                json.dumps(ids),
            )

        return records

    # ------------------------------------------------------------------
    # list / update
    # ------------------------------------------------------------------

    async def list_(
        self,
        user_id: str,
        kind: str | None = None,
        state: str = "active",
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[MemoryRecord]:
        if kind and cursor:
            rows = await self._store.fetch_all(
                """SELECT * FROM memory_records
                   WHERE user_id = ? AND kind = ? AND state = ? AND created_at < ?
                   ORDER BY created_at DESC LIMIT ?""",
                user_id,
                kind,
                state,
                cursor,
                limit,
            )
        elif kind:
            rows = await self._store.fetch_all(
                """SELECT * FROM memory_records
                   WHERE user_id = ? AND kind = ? AND state = ?
                   ORDER BY created_at DESC LIMIT ?""",
                user_id,
                kind,
                state,
                limit,
            )
        elif cursor:
            rows = await self._store.fetch_all(
                """SELECT * FROM memory_records
                   WHERE user_id = ? AND state = ? AND created_at < ?
                   ORDER BY created_at DESC LIMIT ?""",
                user_id,
                state,
                cursor,
                limit,
            )
        else:
            rows = await self._store.fetch_all(
                """SELECT * FROM memory_records
                   WHERE user_id = ? AND state = ?
                   ORDER BY created_at DESC LIMIT ?""",
                user_id,
                state,
                limit,
            )
        return [self._row_to_record(r) for r in rows]

    async def update(
        self, memory_id: str, user_id: str, **fields: Any
    ) -> None:
        allowed = {"content", "kind", "importance", "confidence", "state", "tags"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = now_ts()
        if "tags" in updates:
            updates["tags"] = json.dumps(updates["tags"], ensure_ascii=False)

        sets = [f"{k} = ?" for k in updates]
        params = list(updates.values()) + [memory_id, user_id]
        await self._store.execute(
            f"UPDATE memory_records SET {', '.join(sets)} WHERE memory_id = ? AND user_id = ?",
            *params,
        )

        # if state changed to deleted, remove from indexes
        if updates.get("state") == "deleted":
            try:
                await self._vector.delete("memory", [memory_id])
                await self._keyword.delete("memory", [memory_id])
            except Exception:
                pass

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    async def _get_record(self, memory_id: str, user_id: str) -> MemoryRecord | None:
        row = await self._store.fetch_one(
            "SELECT * FROM memory_records WHERE memory_id = ? AND user_id = ?",
            memory_id,
            user_id,
        )
        return self._row_to_record(row) if row else None

    async def _merge(
        self, existing: MemoryRecord, new_content: str, importance: float
    ) -> MemoryRecord:
        merged = f"{existing.content}\n{new_content}"
        ts = now_ts()
        await self._store.execute(
            """UPDATE memory_records SET content = ?, importance = MAX(importance, ?),
               updated_at = ? WHERE memory_id = ?""",
            merged,
            importance,
            ts,
            existing.memory_id,
        )
        existing.content = merged
        existing.importance = max(existing.importance, importance)
        return existing

    async def _check_conflicts(
        self,
        user_id: str,
        kind: str,
        content: str,
        candidates: list[VectorHit],
    ) -> None:
        if not candidates:
            return
        records = []
        for h in candidates:
            r = await self._get_record(h.id, user_id)
            if r is not None and r.state == "active":
                records.append(r)

        if not records:
            return

        # stub: simple keyword overlap conflict detection
        # TODO: use LLM for semantic conflict detection in Phase 3
        for r in records:
            words_existing = set(r.content.lower().split())
            words_new = set(content.lower().split())
            overlap = len(words_existing & words_new) / max(len(words_new), 1)
            if overlap < 0.3 and len(words_existing) > 3:
                await self.update(
                    r.memory_id,
                    user_id,
                    state="contradicted",
                )

    @staticmethod
    def _fuse_hits(
        vec_hits: list[VectorHit],
        kw_hits: list[KeywordHit],
        vec_weight: float = 0.7,
        kw_weight: float = 0.3,
    ) -> list[_FusedHit]:
        # min-max normalization
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
            fused[h.id] = _FusedHit(
                memory_id=h.id,
                score=vec_weight * vec_norm.get(h.id, 0.0),
                metadata=h.metadata,
            )
        for kh in kw_hits:
            s = kw_weight * kw_norm.get(kh.id, 0.0)
            if kh.id in fused:
                fused[kh.id].score += s
            else:
                fused[kh.id] = _FusedHit(memory_id=kh.id, score=s, metadata=kh.metadata)

        result = list(fused.values())
        result.sort(key=lambda h: -h.score)
        return result

    @staticmethod
    def _apply_boost(score: float, record: MemoryRecord) -> float:
        boosted = score * 0.8
        if record.last_recalled_at:
            days = (now_ts() - record.last_recalled_at) / 86400
            boosted += math.exp(-days / 30) * 0.2
        else:
            boosted += 0.1
        boosted += record.importance * 0.1
        return boosted

    @staticmethod
    def _mmr_select(
        hits: list[_FusedHit],
        query_vec: list[float],
        top_k: int,
        lambda_: float = 0.5,
    ) -> list[int]:
        scores = [h.score for h in hits]
        selected: list[int] = []
        remaining = list(range(len(hits)))

        while remaining and len(selected) < top_k:
            best = -1
            best_score = -float("inf")
            for idx in remaining:
                relevance = scores[idx]
                diversity = 0.0
                if selected:
                    # use dot product of normalized scores as diversity proxy
                    # since we don't have chunk vectors for all hits
                    max_sim = max(
                        (1.0 - abs(scores[idx] - scores[s]) / max(abs(scores[idx] - scores[s]), 0.001))
                        for s in selected
                    ) if selected else 0.0
                    diversity = 1.0 - max_sim
                else:
                    diversity = 0.0
                mmr = lambda_ * relevance + (1 - lambda_) * diversity
                if mmr > best_score:
                    best_score = mmr
                    best = idx
            if best >= 0:
                selected.append(remaining.pop(remaining.index(best)))
            else:
                break
        return selected

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["memory_id"],
            user_id=row["user_id"],
            kind=row["kind"],
            content=row["content"],
            source_type=row.get("source_type", "explicit"),
            source_run_id=row.get("source_run_id"),
            source_session_id=row.get("source_session_id"),
            created_at=row.get("created_at", 0.0),
            updated_at=row.get("updated_at", 0.0),
            last_recalled_at=row.get("last_recalled_at"),
            occurred_at=row.get("occurred_at"),
            confidence=row.get("confidence", 1.0),
            importance=row.get("importance", 0.5),
            recall_count=row.get("recall_count", 0),
            state=row.get("state", "active"),
            tags=_json_list(row.get("tags")),
            metadata=_json_dict(row.get("metadata")),
        )


def _json_list(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, list):
        return raw
    return []


def _json_dict(raw: object) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    return {}
