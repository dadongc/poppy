# 00 · 整体架构总览

> 本文是**全局心智模型**：读完后你应该理解所有模块的相对位置和协作关系。

---

## 1. 系统目标

构建一个**单租户个人 AI 助理**，具备：

- 多轮对话 + 长期记忆
- 私域知识检索（用户文档/网页/笔记）
- 工具调用（飞书、邮件、日历、Web 搜索...）
- 多 Agent 协作（主 Agent 派发子 Agent）
- 流式实时响应
- 可中断、可观测、可回放

部署形态：**单进程 Python（FastAPI）+ 阿里云托管基础设施**。

---

## 2. 分层拓扑

```
┌────────────────────────────────────────────────────────────────────────┐
│                         Client（Web / CLI）                            │
└────────────────┬─────────────────────────────────────┬─────────────────┘
                 │ HTTP POST /run                      │ SSE /run/{id}/events
                 ↓                                     ↑
┌────────────────────────────────────────────────────────────────────────┐
│                     Layer 6 · Gateway 层（FastAPI）                    │
│   Auth │ RateLimit │ Validate │ TraceID │ SSEAdapter │ UploadHandler   │
└────────────────┬─────────────────────────────────────┬─────────────────┘
                 ↓                                     ↑
┌────────────────────────────────────────────────────────────────────────┐
│                     Layer 5 · Runtime（单例）                          │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │
│   │AgentRegistry │  │  EventBus    │  │ RunRegistry  │                │
│   └──────────────┘  └──────────────┘  └──────────────┘                │
└────────────────┬───────────────────────────────────────────────────────┘
                 ↓ create Orchestrator(run_id)
┌────────────────────────────────────────────────────────────────────────┐
│                Layer 4 · Orchestrator(per request)                     │
│                          │                                             │
│                          ↓                                             │
│                Layer 3 · BaseAgent(ReAct Loop)                         │
│   ┌──────────────────────────────────────────────────────────┐        │
│   │   perceive → plan → act → observe → reflect              │        │
│   └─────┬────────┬──────────┬──────────┬──────────┬──────────┘        │
│         ↓        ↓          ↓          ↓          ↓                   │
│  ContextBuilder LLMGateway ToolExecutor ArtifactStore SubAgent        │
└────────┬───────────┬───────────┬───────────┬───────────┬───────────────┘
         ↓           ↓           ↓           ↓           ↓
┌────────────────────────────────────────────────────────────────────────┐
│                       Layer 2 · Service 层                             │
│  Session │ Memory │ Retriever │ KB │ Artifact │ Embedding │ Skill     │
└────────────────┬───────────────────────────────────────────────────────┘
                 ↓
┌────────────────────────────────────────────────────────────────────────┐
│                       Layer 1 · Infra 层                               │
│  RDS PG (pgvector + tsvector) │ Redis │ OSS │ asyncio.Queue │ Jobs    │
└────────────────────────────────────────────────────────────────────────┘
```

各层职责：

| Layer | 职责 | 是否有状态 |
|---|---|---|
| 6 · Gateway | HTTP 入口、协议转换（SSE/WS）、鉴权 | 无（每请求独立） |
| 5 · Runtime | 单例容器，持有全局服务（注册表、EventBus、RunRegistry） | 进程级单例 |
| 4 · Orchestrator | 单次 Run 的执行编排，管理 Agent 生命周期 | per-Run |
| 3 · BaseAgent | ReAct loop 核心循环 | per-Run |
| 2 · Service | 业务逻辑（会话/记忆/知识/产物） | 无状态（持久化在 Infra） |
| 1 · Infra | 存储与基础组件 Protocol 实现 | 持久化层 |

---

## 3. 模块职责一览

### 3.1 Runtime 层组件

| 模块 | 职责 | 关键决策 |
|---|---|---|
| **Runtime** | 进程级单例，组装并持有所有全局服务 | DI 容器、生命周期管理 |
| **AgentRegistry** | Agent 静态定义注册与查找 | 文件加载 + mtime 热更新 |
| **EventBus** | 进程内事件分发 | asyncio.Queue + Subscriber |
| **RunRegistry** | Run 状态机管理 + 树形关系（父子 Agent） | 闭包表 + 取消级联 |

### 3.2 Orchestrator / Agent

| 模块 | 职责 | 关键决策 |
|---|---|---|
| **Orchestrator** | 单次 Run 编排，spawn SubAgent | per-Request 实例 |
| **BaseAgent** | ReAct loop（perceive→plan→act→observe→reflect） | 通用基类，可被特定 Agent 继承 |
| **SubAgent** | BaseAgent 派生实例，独立预算和取消 | parent_run_id 父子关系 |

### 3.3 核心组件

| 模块 | 职责 | 关键决策 |
|---|---|---|
| **ContextBuilder** | 7 段 prompt 组装 | token 预算分级裁剪 |
| **ToolExecutor** | 8 步 pipeline | 并发受 Semaphore 限制 |
| **LLMGateway** | 多 provider 抽象 + 统一 LLMChunk 协议 + retry/fallback | 流式优先、首 chunk 前可重试 |

### 3.4 Service 层

| 模块 | 职责 | 关键决策 |
|---|---|---|
| **SessionService** | 会话消息存储 + rolling summary | 按 session_id 单调 seq |
| **MemoryService** | 长期记忆（8 类）+ 混合检索 + MMR | 显式/隐式/事件三通道写入 |
| **ArtifactStore** | 大对象存储（OSS）+ 元数据管理 | content-hash 去重 + refcount GC |
| **KB** | 知识库文档 + chunk 管理 | 异步 ingest 流水线 |
| **Retriever** | 统一检索（KB + Memory）+ Hybrid + MMR | pgvector + tsvector |
| **EmbeddingGateway** | embedding 调用 + 缓存 + 批处理 | hash(text) → vector 缓存 |

### 3.5 Infra 层

| 接口 | 默认实现 | 备选实现 |
|---|---|---|
| **RelationalStore** | asyncpg + RDS PG | SQLite (本地开发) |
| **VectorIndex** | pgvector | sqlite-vec |
| **KeywordIndex** | PG tsvector（zhparser） | SQLite FTS5 |
| **StorageBackend** | 阿里云 OSS | 本地文件系统 |
| **Cache** | Redis (Tair) | 内存 LRU |
| **EventBus** | asyncio.Queue | （进程内方案是终局） |
| **JobQueue** | PG `async_jobs` 表 + LISTEN/NOTIFY | （PG 是终局） |

### 3.6 Gateway 层

| 路由 | 用途 |
|---|---|
| POST `/api/sessions` | 创建会话 |
| POST `/api/runs` | 启动一次 Agent run |
| GET `/api/runs/{id}/events` | SSE 事件流订阅 |
| POST `/api/runs/{id}/cancel` | 取消 run |
| POST `/api/artifacts` | 上传文件 |
| GET `/api/artifacts/{id}` | 下载/查看 artifact |
| POST `/api/memory/items` | 显式 remember |
| GET `/api/agents` | 列出可用 Agent |

---

## 4. 端到端时序（典型场景）

> 场景：用户已上传 PDF，发起"总结第三章并查 XX 算法新进展，整理成文档发到飞书"

```
Client                Gateway              Runtime/Orchestrator        Services/Infra
  │                     │                          │                         │
  │  POST /runs         │                          │                         │
  │ ──────────────────> │  Auth/Validate           │                         │
  │                     │ ───────────────────────> │ resolve AgentSpec       │
  │                     │                          │ build AgentContext      │
  │                     │                          │ register run (pending)  │
  │                     │                          │ spawn Orchestrator      │
  │  {run_id} <──────── │ <──────────────────────  │                         │
  │                     │                          │                         │
  │  GET /events (SSE)  │                          │                         │
  │ ──────────────────> │  EventBus.subscribe      │                         │
  │                     │                          │                         │
  │                     │                          │ Orchestrator.run()      │
  │                     │                          │  └─ ReAct loop          │
  │                     │                          │                         │
  │                     │                          │  ① PERCEIVE             │
  │                     │                          │   ContextBuilder.build  │
  │                     │                          │     ├ Session.history ──────> PG
  │                     │                          │     ├ Memory.recall ────────> PG (pgvector)
  │                     │                          │     ├ Retriever.kb ─────────> PG (hybrid)
  │                     │                          │     └ assemble payload  │
  │                     │                          │                         │
  │                     │                          │  ② PLAN (LLM stream)    │
  │                     │                          │   LLMGateway.stream ──────> 模型 API
  │  text_delta <══════ SSE ═══════════════════════ EventBus                  │
  │                     │                          │   stop=tool_calls       │
  │                     │                          │     [web_search,        │
  │                     │                          │      generate_doc]      │
  │                     │                          │                         │
  │                     │                          │  ③ ACT                  │
  │                     │                          │   ToolExecutor.execute  │
  │                     │                          │     ├ web_search →      │
  │                     │                          │     │   spawn SubAgent  │
  │                     │                          │     │   "research-agent"│
  │                     │                          │     └ generate_doc →    │
  │                     │                          │         Artifact.save ──────> OSS + PG
  │  tool.completed <══ SSE                        │                         │
  │                     │                          │                         │
  │                     │                          │  ④ OBSERVE → 下一轮     │
  │                     │                          │  ⑤ PLAN+ACT (lark_send) │
  │                     │                          │  ⑥ final_answer         │
  │                     │                          │                         │
  │                     │                          │ run.completed           │
  │  完成事件 <════════ SSE                        │ post-hooks:             │
  │  SSE close                                     │  ├ Session.append ──────────> PG
  │                     │                          │  └ Memory.extract job ──────> PG jobs
```

---

## 5. 数据流向

### 5.1 写入路径

```
用户对话         → SessionService → PG session_messages
                                     ↓ 压缩
                                  MemoryService → PG memory_records + pgvector

用户上传文档     → Gateway       → ArtifactStore → OSS（原文）+ PG（元数据）
                                     ↓ ingest job
                                   KB Worker → PG kb_chunks + pgvector + tsvector

Tool 大输出      → ToolExecutor → ArtifactStore → OSS + 引用注入下一轮 LLM

所有事件         → EventBus     → PG events (持久化, 用于 SSE 回放)
```

### 5.2 读取路径

```
ContextBuilder
   ├── Session.get_recent(session_id, n=20)         → PG
   ├── Memory.recall(query, top_k=10)               → PG pgvector + tsvector hybrid
   ├── Retriever.search(query, channels=[kb])       → PG pgvector + tsvector hybrid + MMR
   └── ArtifactStore.get_summary(artifact_id)       → PG metadata

LLM 输出引用 <ref artifact="..." chunk="..."/>
   ↓
前端点击 → Gateway → ArtifactStore.get_content       → OSS
```

---

## 6. 取消 / 预算 / 超时模型

每个 AgentContext 持有：

```python
cancel_event: asyncio.Event       # per-Run 独立
deadline_at: float                # 绝对时间戳
token_budget: int                 # 累计 token 上限
max_steps: int                    # ReAct 迭代次数上限
```

**取消传播**：
- 用户调 `POST /runs/{id}/cancel` → RunRegistry 查闭包表所有后代 → 逐个 `cancel_event.set()`
- 所有 await 点必须监听 cancel_event（asyncio.wait + FIRST_COMPLETED 模式）

**预算扣减**：
- 每次 LLM 调用结束后从 `usage` 累加 token
- 超过 budget → 抛 `BudgetExceededError`，run 状态变为 `failed`
- SubAgent 持有独立 budget（不与父共享）

**超时**：
- `asyncio.wait_for(coro, timeout=remaining)` 包裹关键调用
- deadline 到达 → 标记 `timeout` 状态

---

## 7. 错误与重试策略

| 层级 | 错误类型 | 处理 |
|---|---|---|
| Infra | 数据库连接失败 | 抛 InfraError，上层不重试，让请求失败 |
| LLMGateway | API 限流/超时 | 首 chunk 前可重试（最多 N 次） + fallback model |
| LLMGateway | 首 chunk 后失败 | 不重试（流已开始，重试会重复输出） |
| ToolExecutor | 工具失败 | 返回 ToolResult(status=error)，让 LLM 决定是否重试 |
| ToolExecutor | 工具超时 | 取消执行，返回 ToolResult(status=timeout) |
| Orchestrator | ReAct loop 异常 | 标记 run failed，publish error 事件 |
| Gateway | 客户端请求异常 | 标准化错误响应 |

---

## 8. 可观测性

**日志**（structlog）：
- 每条日志带 `run_id`, `session_id`, `user_id`, `trace_id`
- 关键路径 INFO，详细执行 DEBUG

**Event**（持久化）：
- `run.started` / `run.completed` / `run.failed` / `run.cancelled`
- `llm.text_delta` / `llm.tool_call_*` / `llm.usage`
- `tool.started` / `tool.completed` / `tool.failed`
- `session.message_added` / `session.summarized`
- `memory.extracted` / `memory.written`
- `artifact.created` / `artifact.deleted`

**Metrics**（后续接 Prometheus）：
- `run_duration_seconds`, `llm_tokens_total`, `tool_calls_total`, `active_runs`

---

## 9. 安全与权限

**鉴权**（个人版）：
- 静态 API Key（环境变量配置）
- HTTP Header `Authorization: Bearer <key>`

**权限**（per-User 隔离 + per-Agent 限制）：
- 所有数据表带 `user_id` 列，查询必带 `WHERE user_id=$1`
- AgentSpec 限定 `allowed_tools` 集合
- ToolExecutor 检查 `tool.scopes ⊆ user.granted_scopes`

**敏感数据**：
- API Key、OAuth Token 走 secrets 管理
- Memory 不写入身份证号、银行卡等高敏字段

---

## 10. 演进路线

| 阶段 | 触发条件 | 升级动作 |
|---|---|---|
| **MVP（当前）** | 单实例、单用户 | 见本文档 |
| **多实例** | 单 ECS 撑不住 | 加 Redis Pub/Sub 用于 SSE 跨实例广播 |
| **多 Worker** | 异步任务积压 | 已有 PG Jobs 表，单独跑 Worker 进程即可 |
| **多用户 SaaS** | 真正商业化 | 加用户管理 + 计费 + 配额 |
| **真正分布式** | 任务量 > 10 万/天 | 异步层从 PG Jobs 切到 RocketMQ |

---

## 11. 关键设计决策（FAQ）

**Q: 为什么不用 LangChain / AutoGen？**
A: 框架黑盒、抽象漏洞多、流式和取消支持差。手写一套清晰的 ReAct loop 比集成框架更可控。

**Q: 为什么 PG 一库三用，不分离 vector DB？**
A: 单租户场景，分离纯增加运维和延迟。pgvector + HNSW 性能对个人量级足够。

**Q: 为什么 EventBus 选进程内 asyncio.Queue 而不是 Redis Streams？**
A: 同进程跨进程 RPC 是负优化。多实例部署再加 Redis 桥接。

**Q: 为什么不用 Celery 做异步任务？**
A: 非 asyncio 原生、配置繁琐、依赖重。PG `async_jobs` 表 + LISTEN/NOTIFY 一张表搞定。

**Q: SubAgent 和普通工具调用的本质区别？**
A: 工具是函数调用，SubAgent 是"派生一个完整 Agent"。SubAgent 有自己的 ReAct loop、context、budget、cancel_event。

**Q: ContextBuilder 为什么不让 LLM 自主决定带什么上下文？**
A: 对个人助理场景，确定性 > 灵活性。让模型每轮都决策"要不要召回 memory"会大幅增加 token 消耗和不稳定性。