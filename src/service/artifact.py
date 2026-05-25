from __future__ import annotations

import hashlib
import json
from typing import Any

from src.common.clock import now_ts
from src.common.errors import NotFoundError
from src.common.ids import new_id
from src.common.types import Artifact, Event, EventType
from src.infra.protocols import EventBus, RelationalStore, StorageBackend
from src.service.llm_protocol import LLMService


class ArtifactSummarizer:
    def __init__(self, llm: LLMService) -> None:
        self._llm = llm

    async def summarize(self, data: bytes, mime_type: str) -> str:
        if mime_type.startswith("text/") or mime_type == "application/json":
            text = data.decode("utf-8", errors="replace")
            return text if len(text) <= 500 else await self._summarize_text(text)
        if mime_type == "application/pdf":
            return f"[pdf, size={len(data)} bytes]"
        if mime_type.startswith("image/"):
            return f"[image, {mime_type}, size={len(data)} bytes]"
        return f"[binary, {mime_type}, size={len(data)} bytes]"

    async def _summarize_text(self, text: str, max_tokens: int = 200) -> str:
        prompt = f"用 100 字以内总结以下内容核心要点：\n\n{text[:8000]}"
        return await self._llm.complete_simple(prompt, max_tokens=max_tokens)


class ArtifactStore:
    INLINE_THRESHOLD = 4096

    def __init__(
        self,
        *,
        store: RelationalStore,
        blob: StorageBackend,
        event_bus: EventBus,
        summarizer: ArtifactSummarizer | None = None,
    ) -> None:
        self._store = store
        self._blob = blob
        self._event_bus = event_bus
        self._summarizer = summarizer

    async def init(self) -> None:
        await self._store.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                storage_uri TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                mime_type TEXT NOT NULL,
                encoding TEXT DEFAULT 'utf-8',
                summary TEXT DEFAULT '',
                preview TEXT,
                source_type TEXT NOT NULL,
                source_run_id TEXT DEFAULT '',
                source_session_id TEXT DEFAULT '',
                source_tool_name TEXT DEFAULT '',
                source_call_id TEXT DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0,
                last_accessed_at REAL NOT NULL DEFAULT 0,
                access_count INTEGER DEFAULT 0,
                state TEXT DEFAULT 'active',
                expires_at REAL,
                pinned INTEGER DEFAULT 0,
                title TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}'
            )
        """)
        await self._store.execute("""
            CREATE TABLE IF NOT EXISTS artifact_blob_refs (
                user_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                storage_uri TEXT NOT NULL,
                refcount INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (user_id, content_hash)
            )
        """)

    async def save(
        self,
        *,
        user_id: str,
        content: str | bytes,
        mime_type: str = "application/octet-stream",
        source_type: str = "user_upload",
        summary: str | None = None,
        title: str = "",
        tags: list[str] | None = None,
        source_run_id: str | None = None,
        source_session_id: str | None = None,
        source_tool_name: str | None = None,
        source_call_id: str | None = None,
        ttl_sec: int | None = None,
        metadata: dict | None = None,
    ) -> Artifact:
        data = content.encode("utf-8") if isinstance(content, str) else content
        content_hash = hashlib.sha256(data).hexdigest()
        size = len(data)
        ts = now_ts()

        async with self._store.transaction() as tx:
            existing = await tx.fetch_one(
                "SELECT * FROM artifact_blob_refs WHERE user_id = ? AND content_hash = ?",
                user_id,
                content_hash,
            )

            if existing:
                storage_uri = existing["storage_uri"]
                await tx.execute(
                    "UPDATE artifact_blob_refs SET refcount = refcount + 1 WHERE user_id = ? AND content_hash = ?",
                    user_id,
                    content_hash,
                )
            else:
                key = f"{user_id}/{content_hash[:2]}/{content_hash}"
                storage_uri = await self._blob.put(key, data, mime_type)
                await tx.execute(
                    "INSERT INTO artifact_blob_refs(user_id, content_hash, storage_uri, refcount) "
                    "VALUES (?, ?, ?, 1)",
                    user_id,
                    content_hash,
                    storage_uri,
                )

            if summary is None and self._summarizer is not None:
                summary = await self._summarizer.summarize(data, mime_type)

            artifact_id = new_id("atf")
            expires_at = (ts + ttl_sec) if ttl_sec is not None else None
            await tx.execute(
                """INSERT INTO artifacts(
                   artifact_id, user_id, storage_uri, content_hash, size_bytes, mime_type,
                   summary, source_type, source_run_id, source_session_id,
                   source_tool_name, source_call_id, created_at, last_accessed_at,
                   expires_at, title, tags, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                artifact_id,
                user_id,
                storage_uri,
                content_hash,
                size,
                mime_type,
                summary or "",
                source_type,
                source_run_id or "",
                source_session_id or "",
                source_tool_name or "",
                source_call_id or "",
                ts,
                ts,
                expires_at,
                title,
                json.dumps(tags or [], ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False),
            )

        artifact = await self.get_metadata(artifact_id, user_id)
        await self._event_bus.publish(
            Event(
                event_id=new_id("evt"),
                type=EventType.ARTIFACT_CREATED,
                run_id=source_run_id or "",
                session_id=source_session_id or "",
                user_id=user_id,
                ts=ts,
                payload={"artifact_id": artifact_id, "size": size, "mime": mime_type},
            )
        )
        return artifact

    async def save_stream(
        self,
        *,
        user_id: str,
        stream: Any,
        mime_type: str = "application/octet-stream",
        **kwargs: Any,
    ) -> Artifact:
        chunks: list[bytes] = []
        async for chunk in stream:
            chunks.append(chunk)
        content = b"".join(chunks)
        return await self.save(user_id=user_id, content=content, mime_type=mime_type, **kwargs)

    async def get_metadata(self, artifact_id: str, user_id: str) -> Artifact:
        row = await self._store.fetch_one(
            "SELECT * FROM artifacts WHERE artifact_id = ? AND user_id = ?",
            artifact_id,
            user_id,
        )
        if row is None:
            raise NotFoundError(f"artifact {artifact_id} not found")
        return self._row_to_artifact(row)

    async def get_content(self, artifact_id: str, user_id: str) -> bytes:
        artifact = await self.get_metadata(artifact_id, user_id)
        data = await self._blob.get(self._extract_key_from_uri(artifact.storage_uri))
        ts = now_ts()
        await self._store.execute(
            """UPDATE artifacts SET last_accessed_at = ?, access_count = access_count + 1
               WHERE artifact_id = ?""",
            ts,
            artifact_id,
        )
        return data

    async def get_text(self, artifact_id: str, user_id: str) -> str:
        data = await self.get_content(artifact_id, user_id)
        artifact = await self.get_metadata(artifact_id, user_id)
        return data.decode(artifact.encoding, errors="replace")

    async def get_stream(self, artifact_id: str, user_id: str) -> Any:
        artifact = await self.get_metadata(artifact_id, user_id)
        return self._blob.get_stream(self._extract_key_from_uri(artifact.storage_uri))

    async def get_signed_url(self, artifact_id: str, user_id: str, expires_in: int = 3600) -> str:
        artifact = await self.get_metadata(artifact_id, user_id)
        return await self._blob.signed_url(
            self._extract_key_from_uri(artifact.storage_uri), expires_in
        )

    async def list_digests(self, limit: int = 30) -> list[dict]:
        """列出每日日报 artifact（按日期去重，取最新）。"""
        rows = await self._store.fetch_all(
            """SELECT a.artifact_id, a.title, a.created_at, a.size_bytes FROM artifacts a
               INNER JOIN (
                   SELECT title, MAX(created_at) as max_ts FROM artifacts
                   WHERE title LIKE 'daily-digest/%' AND size_bytes > 1000
                   GROUP BY title
               ) b ON a.title = b.title AND a.created_at = b.max_ts
               ORDER BY a.created_at DESC LIMIT ?""",
            limit,
        )
        return [dict(r) for r in rows]

    async def update(
        self,
        artifact_id: str,
        user_id: str,
        *,
        title: str | None = None,
        tags: list[str] | None = None,
        pinned: bool | None = None,
        expires_at: float | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(tags, ensure_ascii=False))
        if pinned is not None:
            sets.append("pinned = ?")
            params.append(1 if pinned else 0)
        if expires_at is not None:
            sets.append("expires_at = ?")
            params.append(expires_at)
        if not sets:
            return
        params.extend([artifact_id, user_id])
        await self._store.execute(
            f"UPDATE artifacts SET {', '.join(sets)} WHERE artifact_id = ? AND user_id = ?",
            *params,
        )

    async def archive(self, artifact_id: str, user_id: str) -> None:
        await self._store.execute(
            "UPDATE artifacts SET state = 'archived' WHERE artifact_id = ? AND user_id = ?",
            artifact_id,
            user_id,
        )

    async def delete(self, artifact_id: str, user_id: str) -> None:
        await self._do_delete(artifact_id, user_id)
        await self._event_bus.publish(
            Event(
                event_id=new_id("evt"),
                type=EventType.ARTIFACT_DELETED,
                run_id="",
                user_id=user_id,
                ts=now_ts(),
                payload={"artifact_id": artifact_id},
            )
        )

    async def gc(self, batch_size: int = 100) -> int:
        deleted_blobs = 0
        ts = now_ts()

        expired = await self._store.fetch_all(
            """SELECT artifact_id, user_id FROM artifacts
               WHERE state = 'active' AND pinned = 0
                 AND expires_at IS NOT NULL AND expires_at < ?
               LIMIT ?""",
            ts,
            batch_size,
        )
        for r in expired:
            await self._do_delete(r["artifact_id"], r["user_id"])

        orphans = await self._store.fetch_all(
            "SELECT user_id, content_hash, storage_uri FROM artifact_blob_refs WHERE refcount <= 0 LIMIT ?",
            batch_size,
        )
        for r in orphans:
            try:
                await self._blob.delete(self._extract_key_from_uri(r["storage_uri"]))
                await self._store.execute(
                    "DELETE FROM artifact_blob_refs WHERE user_id = ? AND content_hash = ?",
                    r["user_id"],
                    r["content_hash"],
                )
                deleted_blobs += 1
            except Exception:
                pass
        return deleted_blobs

    async def render_reference(self, artifact: Artifact) -> str:
        return (
            f'<artifact id="{artifact.artifact_id}" '
            f'mime="{artifact.mime_type}" size="{artifact.size_bytes}">\n'
            f"摘要：{artifact.summary}\n"
            f'调用 read_artifact(artifact_id="{artifact.artifact_id}") 获取全文\n'
            f"</artifact>"
        )

    async def _do_delete(self, artifact_id: str, user_id: str) -> None:
        row = await self._store.fetch_one(
            "SELECT content_hash, user_id FROM artifacts WHERE artifact_id = ? AND state = 'active'",
            artifact_id,
        )
        if row is None:
            return
        await self._store.execute(
            "UPDATE artifacts SET state = 'deleted' WHERE artifact_id = ?",
            artifact_id,
        )
        await self._store.execute(
            "UPDATE artifact_blob_refs SET refcount = refcount - 1 WHERE user_id = ? AND content_hash = ?",
            row["user_id"],
            row["content_hash"],
        )

    @staticmethod
    def _extract_key_from_uri(uri: str) -> str:
        if "://" not in uri:
            return uri
        path = uri.split("://", 1)[1]
        # oss://bucket/key → key
        if uri.startswith("oss://"):
            return path.split("/", 1)[1] if "/" in path else path
        # fs:///absolute/path → /absolute/path
        return path

    @staticmethod
    def _row_to_artifact(row: dict[str, Any]) -> Artifact:
        return Artifact(
            artifact_id=row["artifact_id"],
            user_id=row["user_id"],
            storage_uri=row["storage_uri"],
            content_hash=row["content_hash"],
            size_bytes=row["size_bytes"],
            mime_type=row["mime_type"],
            encoding=row.get("encoding", "utf-8"),
            summary=row.get("summary", ""),
            preview=row.get("preview"),
            source_type=row["source_type"],
            source_run_id=row.get("source_run_id"),
            source_session_id=row.get("source_session_id"),
            source_tool_name=row.get("source_tool_name"),
            source_call_id=row.get("source_call_id"),
            created_at=row.get("created_at", 0.0),
            last_accessed_at=row.get("last_accessed_at", 0.0),
            access_count=row.get("access_count", 0),
            state=row.get("state", "active"),
            expires_at=row.get("expires_at"),
            pinned=bool(row.get("pinned", 0)),
            title=row.get("title", ""),
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
