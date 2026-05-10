from __future__ import annotations

import json
from typing import Any

from src.common.clock import now_ts
from src.common.errors import NotFoundError
from src.common.ids import new_id
from src.common.types import Event, EventType, KBDocument, VectorItem
from src.infra.protocols import (
    EventBus,
    JobQueue,
    KeywordIndex,
    RelationalStore,
    VectorIndex,
)
from src.service.artifact import ArtifactStore
from src.service.embedding.gateway import EmbeddingGateway
from src.service.kb.chunker import Chunker
from src.service.kb.loader import load_content


class KBService:
    def __init__(
        self,
        *,
        store: RelationalStore,
        artifact: ArtifactStore,
        jobs: JobQueue | None,
        event_bus: EventBus,
        embedding: EmbeddingGateway,
        vector: VectorIndex,
        keyword: KeywordIndex,
        chunker: Chunker,
    ) -> None:
        self._store = store
        self._artifact = artifact
        self._jobs = jobs
        self._event_bus = event_bus
        self._embedding = embedding
        self._vector = vector
        self._keyword = keyword
        self._chunker = chunker

    async def init(self) -> None:
        await self._store.execute("""
            CREATE TABLE IF NOT EXISTS kb_documents (
                doc_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_uri TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                state TEXT DEFAULT 'ingesting',
                chunk_count INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            )
        """)
        await self._store.execute("""
            CREATE TABLE IF NOT EXISTS kb_chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                text TEXT NOT NULL,
                token_count INTEGER DEFAULT 0,
                embedding_model TEXT NOT NULL,
                char_start INTEGER DEFAULT 0,
                char_end INTEGER DEFAULT 0,
                heading_path TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}'
            )
        """)

    async def add_document(
        self,
        *,
        user_id: str,
        artifact_id: str,
        title: str,
        source_type: str = "upload",
        source_uri: str = "",
        tags: list[str] | None = None,
    ) -> KBDocument:
        doc_id = new_id("doc")
        ts = now_ts()
        await self._store.execute(
            """INSERT INTO kb_documents(doc_id, user_id, artifact_id, title, source_type,
               source_uri, tags, state, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'ingesting', ?, ?)""",
            doc_id,
            user_id,
            artifact_id,
            title,
            source_type,
            source_uri,
            json.dumps(tags or [], ensure_ascii=False),
            ts,
            ts,
        )
        await self._event_bus.publish(
            Event(
                event_id=new_id("evt"),
                type=EventType.KB_DOC_INGESTING,
                run_id="",
                user_id=user_id,
                ts=ts,
                payload={"doc_id": doc_id, "artifact_id": artifact_id},
            )
        )

        if self._jobs is not None:
            await self._jobs.enqueue(
                "kb.ingest",
                {"doc_id": doc_id, "user_id": user_id, "artifact_id": artifact_id},
                max_retries=3,
            )
        else:
            await self.ingest(doc_id, user_id, artifact_id)

        return await self.get_document(doc_id, user_id)

    async def get_document(self, doc_id: str, user_id: str) -> KBDocument:
        row = await self._store.fetch_one(
            "SELECT * FROM kb_documents WHERE doc_id = ? AND user_id = ?",
            doc_id,
            user_id,
        )
        if row is None:
            raise NotFoundError(f"kb document {doc_id} not found")
        return self._row_to_doc(row)

    async def list_documents(
        self,
        user_id: str,
        state: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[KBDocument]:
        if state and cursor:
            rows = await self._store.fetch_all(
                """SELECT * FROM kb_documents
                   WHERE user_id = ? AND state = ? AND created_at < ?
                   ORDER BY created_at DESC LIMIT ?""",
                user_id,
                state,
                cursor,
                limit,
            )
        elif state:
            rows = await self._store.fetch_all(
                "SELECT * FROM kb_documents WHERE user_id = ? AND state = ? ORDER BY created_at DESC LIMIT ?",
                user_id,
                state,
                limit,
            )
        elif cursor:
            rows = await self._store.fetch_all(
                """SELECT * FROM kb_documents
                   WHERE user_id = ? AND created_at < ?
                   ORDER BY created_at DESC LIMIT ?""",
                user_id,
                cursor,
                limit,
            )
        else:
            rows = await self._store.fetch_all(
                "SELECT * FROM kb_documents WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                user_id,
                limit,
            )
        return [self._row_to_doc(r) for r in rows]

    async def delete_document(self, doc_id: str, user_id: str) -> None:
        _ = await self.get_document(doc_id, user_id)  # validate existence
        chunks = await self._store.fetch_all(
            "SELECT chunk_id FROM kb_chunks WHERE doc_id = ?", doc_id
        )
        chunk_ids = [c["chunk_id"] for c in chunks]
        if chunk_ids:
            await self._vector.delete("kb_chunks", chunk_ids)
            await self._keyword.delete("kb_chunks", chunk_ids)
        await self._store.execute(
            "DELETE FROM kb_chunks WHERE doc_id = ?", doc_id
        )
        await self._store.execute(
            "UPDATE kb_documents SET state = 'archived', updated_at = ? WHERE doc_id = ?",
            now_ts(),
            doc_id,
        )

    async def ingest(self, doc_id: str, user_id: str, artifact_id: str) -> None:
        try:
            artifact = await self._artifact.get_metadata(artifact_id, user_id)
            content = await self._artifact.get_content(artifact_id, user_id)
            text, structure = load_content(content, artifact.mime_type)
            chunks = self._chunker.chunk(text, structure)
            texts = [c["text"] for c in chunks]
            vectors = await self._embedding.embed(texts)
            ts = now_ts()

            chunk_ids: list[str] = []
            async with self._store.transaction() as tx:
                for i, (c, _vec) in enumerate(zip(chunks, vectors, strict=True)):
                    chunk_id = new_id("ck")
                    chunk_ids.append(chunk_id)
                    await tx.execute(
                        """INSERT INTO kb_chunks(chunk_id, doc_id, user_id, seq, text,
                           token_count, embedding_model, char_start, char_end, heading_path)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        chunk_id,
                        doc_id,
                        user_id,
                        i,
                        c["text"],
                        c.get("token_count", 0),
                        self._embedding.default_model,
                        c.get("char_start", 0),
                        c.get("char_end", 0),
                        json.dumps(c.get("heading_path", []), ensure_ascii=False),
                    )

                await tx.execute(
                    """UPDATE kb_documents SET state = 'ready', chunk_count = ?,
                       updated_at = ? WHERE doc_id = ?""",
                    len(chunks),
                    ts,
                    doc_id,
                )

            # vector/keyword indexing outside the transaction to avoid SQLite lock deadlock
            for i, (chunk_id, vec, c) in enumerate(zip(chunk_ids, vectors, chunks, strict=True)):
                await self._vector.upsert(
                    "kb_chunks",
                    [
                        VectorItem(
                            id=chunk_id,
                            vector=vec,
                            user_id=user_id,
                            metadata={"doc_id": doc_id, "seq": i},
                        )
                    ],
                )
                await self._keyword.index(
                    "kb_chunks",
                    chunk_id,
                    c["text"],
                    user_id,
                    metadata={"doc_id": doc_id, "seq": i},
                )

            await self._event_bus.publish(
                Event(
                    event_id=new_id("evt"),
                    type=EventType.KB_DOC_READY,
                    run_id="",
                    user_id=user_id,
                    ts=ts,
                    payload={"doc_id": doc_id, "chunk_count": len(chunks)},
                )
            )
        except Exception:
            await self._store.execute(
                "UPDATE kb_documents SET state = 'failed', updated_at = ? WHERE doc_id = ?",
                now_ts(),
                doc_id,
            )
            raise

    @staticmethod
    def _row_to_doc(row: dict[str, Any]) -> KBDocument:
        return KBDocument(
            doc_id=row["doc_id"],
            user_id=row["user_id"],
            artifact_id=row["artifact_id"],
            title=row["title"],
            source_type=row["source_type"],
            source_uri=row.get("source_uri", ""),
            tags=_json_loads(row.get("tags")),
            state=row.get("state", "ingesting"),
            chunk_count=row.get("chunk_count", 0),
            created_at=row.get("created_at", 0.0),
            updated_at=row.get("updated_at", 0.0),
            error=row.get("error", ""),
            metadata=_json_loads_dict(row.get("metadata")),
        )


def _json_loads(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, list):
        return raw
    return []


def _json_loads_dict(raw: object) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    return {}
