# 01 · 公共类型定义

> 本文定义所有跨模块共享的数据结构。**任何模块在用到这些类型时，必须从 `src.common.types` 导入，禁止重复定义**。

实现位置：`src/common/types.py`

---

## 1. 设计原则

- 优先用 `@dataclass(slots=True, kw_only=True)`，性能更好且强制关键字参数
- 只有 **API 边界**（Gateway 入参/出参）才用 Pydantic（用于自动校验）
- 内部传递一律用 dataclass，转换由 Gateway 层负责
- 所有 ID 字段用 ULID（字符串），保证时间有序 + 全局唯一
- 时间字段统一用 `float`（Unix 时间戳，秒级），避免时区问题
- 状态字段用 `Literal[...]` 类型，禁止 magic string

---

## 2. ID 与时间

### 2.1 ULID 生成

```python
# src/common/ids.py
from ulid import ULID

def new_id(prefix: str = "") -> str:
    """生成 ULID。可加业务前缀如 'run_xxx'/'msg_xxx'，便于日志识别。"""
    uid = str(ULID())
    return f"{prefix}_{uid}" if prefix else uid

# 常用前缀
RUN_ID = lambda: new_id("run")
SESSION_ID = lambda: new_id("ses")
MSG_ID = lambda: new_id("msg")
EVENT_ID = lambda: new_id("evt")
ARTIFACT_ID = lambda: new_id("atf")
MEMORY_ID = lambda: new_id("mem")
KB_DOC_ID = lambda: new_id("doc")
KB_CHUNK_ID = lambda: new_id("ck")
JOB_ID = lambda: new_id("job")
```

### 2.2 时间工具

```python
# src/common/clock.py
import time

def now_ts() -> float:
    """当前 Unix 时间戳（秒级，含毫秒小数）。"""
    return time.time()

def now_ms() -> int:
    return int(time.time() * 1000)
```

---

## 3. 消息类型（LLM 视角）

```python
from dataclasses import dataclass, field
from typing import Literal, Any

@dataclass(slots=True, kw_only=True)
class Message:
    """LLM 上下文中的消息单元。"""
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    # assistant 消息可能含 tool_calls
    tool_calls: list["ToolCall"] = field(default_factory=list)
    # tool 消息必填
    tool_call_id: str = ""
    name: str = ""  # tool 消息 = tool name；assistant/user 可选用作昵称
    # 元信息（不直接传给 LLM）
    msg_id: str = ""
    created_at: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class ToolCall:
    """模型请求的工具调用。"""
    call_id: str           # 模型生成的唯一标识，匹配 tool 消息的 tool_call_id
    name: str
    arguments: dict        # 已解析的 JSON
    arguments_raw: str = ""  # 原始字符串（流式拼接用）
```

---

## 4. AgentSpec（静态定义）

```python
from typing import Type

@dataclass(slots=True, kw_only=True)
class AgentSpec:
    """Agent 的静态定义。从 YAML 加载或代码注册。"""
    # 基础信息
    name: str
    description: str = ""
    cls: Type["BaseAgent"] | None = None  # None 表示用默认 BaseAgent

    # Prompt
    system_prompt: str = ""

    # 模型配置
    preferred_model: str = ""
    fallback_models: list[str] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 4096          # 单次 LLM 输出上限

    # 工具与技能
    allowed_tools: set[str] = field(default_factory=set)   # tool 名称集合
    allowed_skills: set[str] = field(default_factory=set)  # skill 名称集合

    # 预算控制
    max_steps: int = 20             # ReAct 迭代上限
    token_budget: int = 5000000       # 累计 token 预算
    deadline_sec: int = 180         # 单次 run 最长时间
    max_parallel_tools: int = 3     # 单步内并发工具数

    # 元信息（运行时填充）
    source: Literal["registry", "ephemeral", "code"] = "registry"
    source_path: str = ""           # YAML 文件路径
    mtime: float = 0.0              # 文件修改时间，用于热更新
```

---

## 5. AgentContext（运行时状态）

```python
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.service.session import SessionService
    from src.service.memory import MemoryService
    # ... 其他 service

@dataclass(slots=True, kw_only=True)
class AgentContext:
    """单次 Run 的运行时上下文。Orchestrator 创建，BaseAgent 持有。"""
    # 标识
    run_id: str
    parent_run_id: str | None = None
    session_id: str
    user_id: str
    trace_id: str

    # Spec 引用（不可变）
    spec: AgentSpec

    # 输入
    user_message: Message
    extra_inputs: dict = field(default_factory=dict)

    # 控制
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    deadline_at: float = 0.0          # Unix 时间戳，0 表示无 deadline
    started_at: float = 0.0

    # 累计统计
    used_tokens: int = 0
    used_steps: int = 0

    # 共享服务（运行时注入，不参与 dataclass repr）
    services: "Services" = field(repr=False)


@dataclass(slots=True, kw_only=True)
class Services:
    """跨服务的句柄集合。Runtime 启动时构造，注入到 AgentContext。"""
    session: "SessionService"
    memory: "MemoryService"
    artifact: "ArtifactStore"
    kb: "KBService"
    retriever: "Retriever"
    embedding: "EmbeddingGateway"
    skill: "SkillRegistry"
    tool: "ToolRegistry"
    llm: "LLMGateway"
    event_bus: "EventBus"
    run_registry: "RunRegistry"
    agent_registry: "AgentRegistry"
```

---

## 6. Event（事件总线消息）

```python
@dataclass(slots=True, kw_only=True)
class Event:
    """EventBus 流转的消息单元。"""
    event_id: str                # ULID
    type: str                    # "run.started" / "llm.text_delta" / ...
    run_id: str
    parent_run_id: str | None = None
    session_id: str
    user_id: str
    trace_id: str = ""
    ts: float                    # 创建时间
    seq: int = 0                 # 同 run 内单调递增（由 EventBus 分配）
    payload: dict = field(default_factory=dict)
    level: Literal["debug", "info", "warn", "error"] = "info"
    scope: Literal["public", "internal"] = "public"  # internal 不推送给前端
```

### Event 类型枚举

```python
class EventType:
    # Run 生命周期
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_TIMEOUT = "run.timeout"

    # ReAct 步骤
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"

    # LLM 流式
    LLM_TEXT_DELTA = "llm.text_delta"
    LLM_TOOL_CALL_START = "llm.tool_call_start"
    LLM_TOOL_CALL_DELTA = "llm.tool_call_delta"
    LLM_TOOL_CALL_END = "llm.tool_call_end"
    LLM_USAGE = "llm.usage"
    LLM_STOP = "llm.stop"
    LLM_ERROR = "llm.error"

    # 工具
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"

    # SubAgent
    SUBAGENT_STARTED = "subagent.started"
    SUBAGENT_COMPLETED = "subagent.completed"

    # Session
    SESSION_MESSAGE_ADDED = "session.message_added"
    SESSION_SUMMARIZED = "session.summarized"

    # Memory
    MEMORY_EXTRACTED = "memory.extracted"
    MEMORY_WRITTEN = "memory.written"

    # Artifact
    ARTIFACT_CREATED = "artifact.created"
    ARTIFACT_DELETED = "artifact.deleted"

    # KB
    KB_DOC_INGESTING = "kb.doc.ingesting"
    KB_DOC_READY = "kb.doc.ready"
```

---

## 7. ToolResult（工具执行结果）

```python
@dataclass(slots=True, kw_only=True)
class ToolResult:
    """ToolExecutor 输出，转换为 tool message 进入下一轮 LLM。"""
    call_id: str
    name: str
    status: Literal["ok", "error", "timeout", "cancelled", "denied"]
    content: str = ""              # 注入 LLM 的内容（小输出直接，大输出是 artifact 引用）
    artifact_id: str | None = None # 大输出时引用
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class ExecutionReport:
    """单步多工具执行的汇总（用于事件、日志）。"""
    results: list[ToolResult]
    total_duration_ms: int
    parallel_count: int
    failed_count: int
```

---

## 8. LLMChunk（流式协议）

```python
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
    """LLMGateway 流式输出统一协议。所有 provider 必须转换为这个格式。"""
    type: Literal[
        "text_delta",         # 文本增量
        "tool_call_start",    # 工具调用开始（含 name）
        "tool_call_delta",    # 工具调用参数增量（含 arguments_delta）
        "tool_call_end",      # 工具调用结束（参数完整）
        "usage",              # token 统计
        "stop",               # 流正常结束
        "error",              # 流出错
    ]
    text: str = ""
    tool_call_index: int = -1       # 第几个 tool call（同步骤可有多个）
    tool_call_id: str = ""
    tool_name: str = ""
    arguments_delta: str = ""
    arguments_full: dict | None = None  # tool_call_end 时填充
    usage: Usage | None = None
    stop_reason: Literal["end", "tool_calls", "length", "content_filter"] | None = None
    error: LLMError | None = None
```

---

## 9. Artifact（大对象）

```python
@dataclass(slots=True, kw_only=True)
class Artifact:
    """大对象元信息。原文存储在 storage_uri 指向的位置。"""
    artifact_id: str
    user_id: str

    # 存储
    storage_uri: str               # "oss://bucket/key" / "fs:///path" / "memory://..."
    content_hash: str              # sha256 hex
    size_bytes: int
    mime_type: str
    encoding: str = "utf-8"

    # LLM 视图
    summary: str = ""              # 注入 prompt 的摘要
    preview: str | None = None     # 给前端的预览（如前 500 字 / 缩略图 URL）

    # 来源
    source_type: Literal[
        "user_upload", "tool_output", "subagent_output",
        "session_export", "system"
    ]
    source_run_id: str | None = None
    source_session_id: str | None = None
    source_tool_name: str | None = None
    source_call_id: str | None = None

    # 生命周期
    created_at: float
    last_accessed_at: float
    access_count: int = 0
    state: Literal["active", "archived", "deleted"] = "active"
    expires_at: float | None = None
    pinned: bool = False           # 防止被 GC

    # 业务标签
    title: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
```

---

## 10. SessionMessage（持久化消息）

```python
@dataclass(slots=True, kw_only=True)
class SessionMessage:
    """持久化在 PG session_messages 的消息记录。"""
    msg_id: str
    session_id: str
    user_id: str
    seq: int                       # 会话内单调递增
    run_id: str | None             # 哪个 run 产生的（user 消息可能为空）
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""
    name: str = ""
    artifact_refs: list[str] = field(default_factory=list)  # 引用的 artifact_id
    created_at: float
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class SessionInfo:
    session_id: str
    user_id: str
    title: str = ""
    created_at: float
    last_active_at: float
    message_count: int = 0
    summary: str = ""              # rolling summary
    summary_covers_until_seq: int = 0   # summary 覆盖到第几条消息
    metadata: dict = field(default_factory=dict)
```

---

## 11. MemoryRecord

```python
class MemoryKind:
    PROFILE = "profile"           # 用户身份/属性
    PREFERENCE = "preference"     # 偏好/习惯
    FACT = "fact"                 # 客观事实
    EVENT = "event"               # 一次性事件
    EPISODE = "episode"           # 一段经历（多事件聚合）
    TASK = "task"                 # 待办/计划
    REMINDER = "reminder"         # 提醒
    SKILL_NOTE = "skill_note"     # 关于某技能的使用心得
    RELATION = "relation"         # 人/物关系


@dataclass(slots=True, kw_only=True)
class MemoryRecord:
    memory_id: str
    user_id: str
    kind: str                     # MemoryKind 之一
    content: str                  # 自然语言陈述
    # 来源
    source_type: Literal["explicit", "implicit", "event", "import"]
    source_run_id: str | None = None
    source_session_id: str | None = None
    # 时间
    created_at: float
    updated_at: float
    last_recalled_at: float | None = None
    occurred_at: float | None = None  # 事件型记忆的发生时间
    # 评分
    confidence: float = 1.0       # 0~1，置信度
    importance: float = 0.5       # 0~1，重要性
    recall_count: int = 0
    # 状态
    state: Literal["active", "stale", "contradicted", "deleted"] = "active"
    # 关联
    related_memory_ids: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    # 业务
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    # 向量（不在主 dataclass，由 VectorIndex 单独存）
```

---

## 12. KB 类型

```python
@dataclass(slots=True, kw_only=True)
class KBDocument:
    doc_id: str
    user_id: str
    artifact_id: str               # 指向原文 Artifact
    title: str
    source_type: Literal["upload", "url", "note", "email", "chat_export"]
    source_uri: str = ""
    tags: list[str] = field(default_factory=list)
    state: Literal["ingesting", "ready", "failed", "archived"] = "ingesting"
    chunk_count: int = 0
    created_at: float
    updated_at: float
    error: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class KBChunk:
    chunk_id: str
    doc_id: str
    user_id: str
    seq: int                       # 文档内顺序
    text: str
    token_count: int
    embedding_model: str
    char_start: int = 0
    char_end: int = 0
    heading_path: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
```

---

## 13. RunInfo（RunRegistry 视图）

```python
@dataclass(slots=True, kw_only=True)
class RunInfo:
    run_id: str
    parent_run_id: str | None
    session_id: str
    user_id: str
    agent_name: str
    state: Literal[
        "pending", "running",
        "completed", "failed", "cancelled", "timeout"
    ]
    started_at: float
    finished_at: float | None = None
    error: str = ""
    used_tokens: int = 0
    used_steps: int = 0
    metadata: dict = field(default_factory=dict)
```

---

## 14. PromptPayload（ContextBuilder 输出）

```python
@dataclass(slots=True, kw_only=True)
class PromptPayload:
    """ContextBuilder 产出，喂给 LLMGateway 的完整请求材料。"""
    messages: list[dict]            # OpenAI/Anthropic 兼容格式（dict 形态）
    tools: list[dict]               # JSON Schema 格式
    model: str
    fallback_models: list[str]
    temperature: float
    max_tokens: int
    # 调试信息
    token_estimate: int             # 输入 token 估算
    sections: dict[str, int]        # 每段占用 token，用于观测
    dropped: list[str] = field(default_factory=list)  # 被裁剪掉的内容标签
```

---

## 15. 检索类型

```python
@dataclass(slots=True, kw_only=True)
class RetrievalQuery:
    text: str
    user_id: str
    channels: list[Literal["memory", "kb"]] = field(default_factory=lambda: ["kb"])
    top_k: int = 8
    filters: dict = field(default_factory=dict)  # tags, source_type, time_range
    rerank: bool = False
    diversify: bool = True


@dataclass(slots=True, kw_only=True)
class RetrievalHit:
    channel: Literal["memory", "kb"]
    chunk_id: str                   # 或 memory_id
    doc_id: str | None = None
    artifact_id: str | None = None
    text: str = ""
    score: float = 0.0
    citation: dict = field(default_factory=dict)  # title, heading_path, char_range


@dataclass(slots=True, kw_only=True)
class VectorItem:
    """VectorIndex.upsert 入参。"""
    id: str
    vector: list[float]
    user_id: str
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class VectorHit:
    id: str
    score: float
    metadata: dict = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class KeywordHit:
    id: str
    score: float
    snippet: str = ""
    metadata: dict = field(default_factory=dict)
```

---

## 16. 异常体系

```python
# src/common/errors.py
class AgentError(Exception):
    """所有自定义异常的根。"""

class InfraError(AgentError):
    """基础设施层异常（DB/缓存/存储）。不可恢复。"""

class ConfigError(AgentError):
    """配置错误。启动时抛。"""

class PermissionError(AgentError):
    """权限不足。"""

class NotFoundError(AgentError):
    """资源不存在。"""

class BudgetExceededError(AgentError):
    """token / 时间 / 步数预算耗尽。"""

class CancelledError(AgentError):
    """运行被显式取消。"""

class TimeoutError(AgentError):
    """运行超时。"""

class LLMProviderError(AgentError):
    """LLM provider 调用失败（详见 error.type）。"""
    def __init__(self, msg: str, error: "LLMError"):
        super().__init__(msg)
        self.error = error

class ToolError(AgentError):
    """工具执行失败。"""
    def __init__(self, msg: str, *, tool_name: str = "", error_type: str = "unknown"):
        super().__init__(msg)
        self.tool_name = tool_name
        self.error_type = error_type
```

---

## 17. 配置加载

```python
# src/common/config.py
from pydantic import BaseModel
from pathlib import Path
import yaml

class InfraConfig(BaseModel):
    relational: dict
    vector: dict
    keyword: dict
    blob: dict
    cache: dict
    eventbus: dict

class LLMConfig(BaseModel):
    providers: dict
    default_model: str

class AgentConfig(BaseModel):
    registry_path: str
    skills_path: str

class GatewayConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    api_key: str = ""
    cors_origins: list[str] = []

class AppConfig(BaseModel):
    infra: InfraConfig
    llm: LLMConfig
    agent: AgentConfig
    gateway: GatewayConfig
    embedding: dict
    reranker: dict | None = None

def load_config(path: str | Path) -> AppConfig:
    with open(path) as f:
        data = yaml.safe_load(f)
    return AppConfig(**data)
```

---

## 18. 测试约定（针对 common 模块）

```
tests/unit/common/
├── test_ids.py           # ULID 生成 + 前缀
├── test_clock.py         # 时间工具
├── test_types.py         # dataclass 默认值、序列化
└── test_errors.py        # 异常继承链
```

每个测试文件至少覆盖：
- 默认值是否合理
- 必填字段缺失时报错
- `to_dict()` / `from_dict()` 往返一致（如果有该方法）

---

## 19. 序列化辅助（可选）

```python
# src/common/serde.py
from dataclasses import asdict, is_dataclass
from typing import Any

def to_dict(obj: Any) -> Any:
    """递归把 dataclass 转 dict（含嵌套 dataclass / list / dict）。"""
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, list):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj
```

注意：`asyncio.Event` / Service 句柄等不可序列化字段，在序列化前要先排除（一般通过 `field(repr=False)` + 自定义 `to_dict`）。

---

## 20. 类型间关系图

```
AgentSpec ──┐
            ├──> AgentContext ──> BaseAgent ──> ReAct loop
Services ───┘                                       │
                                                    ├─> Message (in/out)
                                                    ├─> ToolCall ─> ToolResult
                                                    ├─> LLMChunk (stream)
                                                    └─> Event (publish)
                                                         │
                                                  ┌──────┴────────┐
                                                  ↓               ↓
                                            EventBus         RunRegistry
                                                  ↓
                                              Subscribers
                                                  ↓
                                              SSE Adapter

Artifact ←── ArtifactStore ←── ToolExecutor (post-process)

KBDocument ─owns─ KBChunk
MemoryRecord ←── MemoryService

RetrievalQuery → Retriever → RetrievalHit
```