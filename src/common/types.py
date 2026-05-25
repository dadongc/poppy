from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.agent.llm_gateway import LLMGateway
    from src.runtime.agent_registry import AgentRegistry
    from src.runtime.event_bus import EventBus
    from src.runtime.run_registry import RunRegistry
    from src.service.artifact import ArtifactStore
    from src.service.embedding import EmbeddingGateway
    from src.service.kb import KBService
    from src.service.memory import MemoryService
    from src.service.retriever import Retriever
    from src.service.session import SessionService
    from src.skills.registry import SkillRegistry
    from src.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# 3. Message types
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict = field(default_factory=dict)
    arguments_raw: str = ""


@dataclass(slots=True, kw_only=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""
    msg_id: str = ""
    created_at: float = 0.0
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 4. AgentSpec
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class AgentSpec:
    name: str
    description: str = ""
    system_prompt: str = ""
    preferred_model: str = ""
    fallback_models: list[str] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 4096
    allowed_tools: set[str] = field(default_factory=set)
    denied_tools: set[str] = field(default_factory=set)
    allowed_skills: set[str] = field(default_factory=set)
    max_steps: int = 20
    token_budget: int = 5000000
    deadline_sec: int = 180
    max_parallel_tools: int = 3
    max_sub_agent_depth: int = 3
    max_parallel_sub_agents: int = 5
    source: Literal["registry", "ephemeral", "code"] = "registry"
    source_path: str = ""
    mtime: float = 0.0


# ---------------------------------------------------------------------------
# 5. AgentContext & Services
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class Services:
    session: SessionService | None = None
    memory: MemoryService | None = None
    artifact: ArtifactStore | None = None
    kb: KBService | None = None
    retriever: Retriever | None = None
    embedding: EmbeddingGateway | None = None
    skill: SkillRegistry | None = None
    tool: ToolRegistry | None = None
    llm: LLMGateway | None = None
    event_bus: EventBus | None = None
    run_registry: RunRegistry | None = None
    agent_registry: AgentRegistry | None = None


@dataclass(slots=True, kw_only=True)
class AgentContext:
    run_id: str
    parent_run_id: str | None = None
    session_id: str = ""
    user_id: str = ""
    trace_id: str = ""
    depth: int = 0
    spec: AgentSpec | None = None
    user_message: Message | None = None
    extra_inputs: dict = field(default_factory=dict)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    deadline_at: float = 0.0
    started_at: float = 0.0
    used_tokens: int = 0
    used_steps: int = 0
    services: Services = field(default_factory=Services, repr=False)


# ---------------------------------------------------------------------------
# 6. Event
# ---------------------------------------------------------------------------


class EventType:
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_TIMEOUT = "run.timeout"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    LLM_TEXT_DELTA = "llm.text_delta"
    LLM_TOOL_CALL_START = "llm.tool_call_start"
    LLM_TOOL_CALL_DELTA = "llm.tool_call_delta"
    LLM_TOOL_CALL_END = "llm.tool_call_end"
    LLM_USAGE = "llm.usage"
    LLM_STOP = "llm.stop"
    LLM_ERROR = "llm.error"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    SUBAGENT_STARTED = "subagent.started"
    SUBAGENT_COMPLETED = "subagent.completed"
    SESSION_MESSAGE_ADDED = "session.message_added"
    SESSION_SUMMARIZED = "session.summarized"
    MEMORY_EXTRACTED = "memory.extracted"
    MEMORY_WRITTEN = "memory.written"
    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_DELETED = "artifact.deleted"
    KB_DOC_INGESTING = "kb.doc.ingesting"
    KB_DOC_READY = "kb.doc.ready"


@dataclass(slots=True, kw_only=True)
class Event:
    event_id: str
    type: str
    run_id: str
    parent_run_id: str | None = None
    session_id: str = ""
    user_id: str = ""
    trace_id: str = ""
    ts: float = 0.0
    seq: int = 0
    payload: dict = field(default_factory=dict)
    level: Literal["debug", "info", "warn", "error"] = "info"
    scope: Literal["public", "internal"] = "public"


# ---------------------------------------------------------------------------
# 7. ToolResult
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class ToolResult:
    call_id: str
    name: str
    status: Literal["ok", "error", "timeout", "cancelled", "denied"]
    content: str = ""
    artifact_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class ExecutionReport:
    results: list[ToolResult] = field(default_factory=list)
    total_duration_ms: int = 0
    parallel_count: int = 0
    failed_count: int = 0


# ---------------------------------------------------------------------------
# 8. LLMChunk
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(slots=True, kw_only=True)
class LLMError:
    type: Literal["rate_limit", "context_overflow", "auth", "provider", "network", "unknown"]
    message: str
    provider: str = ""
    retryable: bool = False


@dataclass(slots=True, kw_only=True)
class LLMChunk:
    type: Literal[
        "text_delta",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_end",
        "usage",
        "stop",
        "error",
    ]
    text: str = ""
    tool_call_index: int = -1
    tool_call_id: str = ""
    tool_name: str = ""
    arguments_delta: str = ""
    arguments_full: dict | None = None
    usage: Usage | None = None
    stop_reason: Literal["end", "tool_calls", "length", "content_filter"] | None = None
    error: LLMError | None = None


# ---------------------------------------------------------------------------
# 9. Artifact
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class Artifact:
    artifact_id: str
    user_id: str = ""
    storage_uri: str = ""
    content_hash: str = ""
    size_bytes: int = 0
    mime_type: str = ""
    encoding: str = "utf-8"
    summary: str = ""
    preview: str | None = None
    source_type: Literal[
        "user_upload", "tool_output", "subagent_output", "session_export", "system"
    ] = "user_upload"
    source_run_id: str | None = None
    source_session_id: str | None = None
    source_tool_name: str | None = None
    source_call_id: str | None = None
    created_at: float = 0.0
    last_accessed_at: float = 0.0
    access_count: int = 0
    state: Literal["active", "archived", "deleted"] = "active"
    expires_at: float | None = None
    pinned: bool = False
    title: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 10. SessionMessage
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class SessionMessage:
    msg_id: str
    session_id: str
    user_id: str = ""
    seq: int = 0
    run_id: str | None = None
    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    created_at: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class SessionInfo:
    session_id: str
    user_id: str = ""
    title: str = ""
    created_at: float = 0.0
    last_active_at: float = 0.0
    message_count: int = 0
    summary: str = ""
    summary_covers_until_seq: int = 0
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 11. MemoryRecord
# ---------------------------------------------------------------------------


class MemoryKind:
    PROFILE = "profile"
    PREFERENCE = "preference"
    FACT = "fact"
    EVENT = "event"
    EPISODE = "episode"
    TASK = "task"
    REMINDER = "reminder"
    SKILL_NOTE = "skill_note"
    RELATION = "relation"


@dataclass(slots=True, kw_only=True)
class MemoryRecord:
    memory_id: str
    user_id: str = ""
    kind: str = MemoryKind.FACT
    content: str = ""
    source_type: Literal["explicit", "implicit", "event", "import"] = "explicit"
    source_run_id: str | None = None
    source_session_id: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    last_recalled_at: float | None = None
    occurred_at: float | None = None
    confidence: float = 1.0
    importance: float = 0.5
    recall_count: int = 0
    state: Literal["active", "stale", "contradicted", "deleted"] = "active"
    related_memory_ids: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 12. KB types
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class KBDocument:
    doc_id: str
    user_id: str = ""
    artifact_id: str = ""
    title: str = ""
    source_type: Literal["upload", "url", "note", "email", "chat_export"] = "upload"
    source_uri: str = ""
    tags: list[str] = field(default_factory=list)
    state: Literal["ingesting", "ready", "failed", "archived"] = "ingesting"
    chunk_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    error: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class KBChunk:
    chunk_id: str
    doc_id: str
    user_id: str = ""
    seq: int = 0
    text: str = ""
    token_count: int = 0
    embedding_model: str = ""
    char_start: int = 0
    char_end: int = 0
    heading_path: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 13. RunInfo
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class RunInfo:
    run_id: str
    parent_run_id: str | None = None
    session_id: str = ""
    user_id: str = ""
    agent_name: str = ""
    state: Literal["pending", "running", "completed", "failed", "cancelled", "timeout"] = "pending"
    started_at: float = 0.0
    finished_at: float | None = None
    error: str = ""
    used_tokens: int = 0
    used_steps: int = 0
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 14. PromptPayload
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class PromptPayload:
    messages: list[dict] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    model: str = ""
    fallback_models: list[str] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 4096
    token_estimate: int = 0
    sections: dict[str, int] = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 15. Retrieval types
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class RetrievalQuery:
    text: str
    user_id: str
    channels: list[Literal["memory", "kb"]] = field(default_factory=lambda: ["kb"])
    top_k: int = 8
    filters: dict = field(default_factory=dict)
    rerank: bool = False
    diversify: bool = True


@dataclass(slots=True, kw_only=True)
class RetrievalHit:
    channel: Literal["memory", "kb"]
    chunk_id: str = ""
    doc_id: str | None = None
    artifact_id: str | None = None
    text: str = ""
    score: float = 0.0
    citation: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class VectorItem:
    id: str
    vector: list[float] = field(default_factory=list)
    user_id: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class VectorHit:
    id: str
    score: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class KeywordHit:
    id: str
    score: float = 0.0
    snippet: str = ""
    metadata: dict = field(default_factory=dict)
