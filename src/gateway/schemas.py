from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class CreateSessionIn(BaseModel):
    title: str = ""


class CreateSessionOut(BaseModel):
    session_id: str
    created_at: float


class SessionItem(BaseModel):
    session_id: str
    title: str
    created_at: float
    last_active_at: float
    message_count: int


class ListSessionsOut(BaseModel):
    items: list[SessionItem]
    next_cursor: str | None = None


class MessageItem(BaseModel):
    msg_id: str
    session_id: str
    seq: int
    run_id: str | None = None
    role: str
    content: str
    tool_calls: list[dict] = Field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""
    artifact_refs: list[str] = Field(default_factory=list)
    created_at: float = 0.0


class ListMessagesOut(BaseModel):
    messages: list[MessageItem]


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

class StartRunIn(BaseModel):
    session_id: str = ""
    message: str
    agent_name: str = "default"
    artifact_refs: list[str] = Field(default_factory=list)


class StartRunOut(BaseModel):
    run_id: str


class RunInfoOut(BaseModel):
    run_id: str
    parent_run_id: str | None = None
    session_id: str = ""
    user_id: str = ""
    agent_name: str = ""
    state: str = "pending"
    started_at: float = 0.0
    finished_at: float | None = None
    error: str = ""
    used_tokens: int = 0
    used_steps: int = 0


def run_info_to_out(info: Any) -> dict:
    d = asdict(info)
    d.pop("metadata", None)
    return d


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

class ArtifactOut(BaseModel):
    artifact_id: str
    title: str
    mime_type: str
    size_bytes: int
    summary: str = ""
    preview: str | None = None
    source_type: str = "user_upload"
    created_at: float = 0.0
    state: str = "active"


def artifact_to_out(artifact: Any) -> dict:
    return {
        "artifact_id": artifact.artifact_id,
        "title": artifact.title,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "summary": artifact.summary,
        "preview": artifact.preview,
        "source_type": artifact.source_type,
        "created_at": artifact.created_at,
        "state": artifact.state,
    }


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

class RememberIn(BaseModel):
    kind: str = "fact"
    content: str
    importance: float = 0.5
    confidence: float = 1.0
    tags: list[str] = Field(default_factory=list)
    occurred_at: float | None = None


class MemoryItemOut(BaseModel):
    memory_id: str
    kind: str
    content: str
    source_type: str
    created_at: float
    updated_at: float
    importance: float
    confidence: float
    recall_count: int
    state: str
    tags: list[str] = Field(default_factory=list)


def memory_to_out(m: Any) -> dict:
    return {
        "memory_id": m.memory_id,
        "kind": m.kind,
        "content": m.content,
        "source_type": m.source_type,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "importance": m.importance,
        "confidence": m.confidence,
        "recall_count": m.recall_count,
        "state": m.state,
        "tags": m.tags,
    }


class RecallIn(BaseModel):
    query: str
    top_k: int = 10
    kinds: list[str] | None = None


class RecallOut(BaseModel):
    items: list[MemoryItemOut]


class ListMemoryOut(BaseModel):
    items: list[MemoryItemOut]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# KB
# ---------------------------------------------------------------------------

class IngestDocIn(BaseModel):
    artifact_id: str
    title: str = ""
    source_type: str = "upload"
    source_uri: str = ""
    tags: list[str] = Field(default_factory=list)


class IngestDocOut(BaseModel):
    doc_id: str
    state: str


class KBDocItem(BaseModel):
    doc_id: str
    title: str
    source_type: str
    source_uri: str = ""
    state: str
    chunk_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    error: str = ""
    tags: list[str] = Field(default_factory=list)


class ListKBDocsOut(BaseModel):
    items: list[KBDocItem]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class AgentItem(BaseModel):
    name: str
    description: str = ""
    source: str = "registry"
