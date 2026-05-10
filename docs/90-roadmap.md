# 90 · 实施路线图（TODO List）

> 自下而上 · 每个模块一组单元测试 · Phase 完成后必须能独立跑通

---

## Phase 总览

```
Phase 0  · 项目骨架 + 公共类型             (1 天)
Phase 1  · Infra 层 (PG/OSS/Redis/EventBus)  (4 天)
Phase 2  · Service 层 (Session/Memory/KB/...)(5 天)
Phase 3  · Agent 编排 (Runtime/ReAct)        (5 天)
Phase 4  · Gateway 层 (HTTP/SSE)            (2 天)
Phase 5  · Tools / Skills 扩充              (持续)
```

每个 Phase 验收：**全部单元测试通过 + 至少 1 条端到端集成路径跑通**。

---

## Phase 0 · 项目骨架

**目标**：把代码库脚手架立起来,公共类型定义就位。

### 0.1 项目初始化

- [ ] 用 `uv` 或 `poetry` 初始化项目,Python 3.11+
- [ ] `pyproject.toml`：`fastapi`, `uvicorn[standard]`, `asyncpg`, `pgvector`, `redis`, `oss2`, `openai`, `anthropic`, `pydantic>=2`, `pyyaml`, `structlog`, `python-ulid`, `tiktoken`, `jsonschema`, `numpy`, `tenacity`
- [ ] dev deps：`pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `httpx`
- [ ] 目录结构：

```
personal-assistant/
├── src/
│   ├── common/
│   ├── infra/
│   ├── service/
│   ├── agent/
│   ├── runtime/
│   ├── gateway/
│   ├── tools/
│   ├── skills/
│   └── agents/
├── tests/
│   ├── unit/
│   └── integration/
├── migrations/
├── config/
│   ├── dev.yaml
│   └── prod.yaml
├── docker-compose.test.yml
├── pyproject.toml
└── README.md
```

- [ ] 配置 `ruff.toml` + `mypy.ini` + `pytest.ini`
- [ ] `Makefile`：`make test`, `make lint`, `make run`, `make migrate`

### 0.2 公共类型（src/common/）

- [ ] `src/common/ids.py`：ULID 生成
- [ ] `src/common/clock.py`：时间工具
- [ ] `src/common/types.py`：所有 dataclass（详见 `01-common-types.md`）
- [ ] `src/common/errors.py`：异常体系
- [ ] `src/common/serde.py`：序列化辅助
- [ ] `src/common/config.py`：Pydantic AppConfig + load_config

### 0.3 单元测试

```
tests/unit/common/
├── test_ids.py             # ULID 生成 + 前缀
├── test_clock.py           # now_ts / now_ms
├── test_types.py           # 默认值、必填字段
├── test_errors.py          # 异常继承链
├── test_serde.py           # to_dict 往返
└── test_config.py          # YAML 加载
```

**Phase 0 验收**：`pytest tests/unit/common -v` 全部通过;`ruff check` 干净。

---

## Phase 1 · Infra 层

**目标**：所有底层 Protocol 接口 + 默认实现就绪,能跑 docker-compose 起 PG/Redis 验证。

### 1.1 Relational Store

- [ ] `src/infra/relational/protocol.py`：`RelationalStore` + `Transaction` Protocol
- [ ] `src/infra/relational/postgres.py`：asyncpg 实现,连接池 + JSONB codec + LISTEN/NOTIFY
- [ ] `migrations/001_init.sql`：建表 + extension（pgvector, zhparser）
- [ ] `migrations/runner.py`：基于 `_schema_meta` 表的 SQL 顺序执行器
- [ ] tests/unit/infra/relational/test_pg_basic.py（CRUD、事务回滚）
- [ ] tests/unit/infra/relational/test_pg_jsonb.py
- [ ] tests/unit/infra/relational/test_pg_notify.py（LISTEN/NOTIFY）

### 1.2 Vector Index

- [ ] `src/infra/vector/protocol.py`：`VectorIndex` Protocol
- [ ] `src/infra/vector/pgvector_index.py`：HNSW + cosine + namespace 表
- [ ] `migrations/002_vector.sql`
- [ ] tests/unit/infra/vector/test_upsert_search.py
- [ ] tests/unit/infra/vector/test_namespace_isolation.py
- [ ] tests/unit/infra/vector/test_filter_metadata.py

### 1.3 Keyword Index

- [ ] `src/infra/keyword/protocol.py`
- [ ] `src/infra/keyword/pg_tsvector.py`：zhparser + GIN + ts_headline
- [ ] tests/unit/infra/keyword/test_search_chinese.py
- [ ] tests/unit/infra/keyword/test_snippet.py

### 1.4 Storage Backend

- [ ] `src/infra/blob/protocol.py`
- [ ] `src/infra/blob/oss.py`：阿里云 OSS（线程池包装同步 SDK）
- [ ] `src/infra/blob/filesystem.py`：本地 fs 兼容
- [ ] tests/unit/infra/blob/test_fs_backend.py
- [ ] tests/integration/infra/test_oss_backend.py（需真实/mock OSS）

### 1.5 Cache

- [ ] `src/infra/cache/protocol.py`
- [ ] `src/infra/cache/redis_cache.py`
- [ ] `src/infra/cache/memory_cache.py`：本地 LRU fallback
- [ ] tests/unit/infra/cache/test_redis_basic.py
- [ ] tests/unit/infra/cache/test_memory_lru.py

### 1.6 EventBus（进程内）

- [ ] `src/runtime/event_bus.py`：InProcessEventBus + Subscription + EventFilter
- [ ] `src/runtime/event_persister.py`：批量异步落盘 PG
- [ ] tests/unit/runtime/test_eventbus_publish.py
- [ ] tests/unit/runtime/test_eventbus_filter.py
- [ ] tests/unit/runtime/test_eventbus_replay.py
- [ ] tests/unit/runtime/test_eventbus_terminal_flush.py

### 1.7 Job Queue

- [ ] `src/infra/jobs/protocol.py`
- [ ] `src/infra/jobs/pg_jobs.py`：FOR UPDATE SKIP LOCKED + NOTIFY
- [ ] `migrations/003_jobs.sql`
- [ ] tests/unit/infra/jobs/test_enqueue_dequeue.py
- [ ] tests/unit/infra/jobs/test_retry_backoff.py
- [ ] tests/unit/infra/jobs/test_concurrent_workers.py

### 1.8 InfraFactory

- [ ] `src/infra/__init__.py`：`build_infra(config) -> InfraBundle`
- [ ] tests/integration/infra/test_factory_dev.py（用 dev.yaml 装配 + 健康检查）

**Phase 1 验收**：
- 所有单元测试通过（用 SQLite + memory cache 替身）
- 集成测试通过（docker-compose up postgres redis）
- 能用 CLI 跑：`python -m src.infra.smoke_test` 验证 PG/Redis/OSS 连通性

---

## Phase 2 · Service 层

**目标**：业务逻辑组件就绪,可独立跑（不依赖 Agent）。

### 2.1 SessionService

- [ ] `src/service/session.py`：append/get_window_for_context/compress
- [ ] `migrations/010_sessions.sql`
- [ ] tests/unit/service/session/test_append_seq.py
- [ ] tests/unit/service/session/test_window_token_limit.py
- [ ] tests/unit/service/session/test_tool_pair_atomic.py
- [ ] tests/unit/service/session/test_compress_summary.py

### 2.2 ArtifactStore

- [ ] `src/service/artifact.py`：save/save_stream/get/read/signed_url/delete
- [ ] `src/service/artifact_summarizer.py`
- [ ] `src/service/artifact_gc.py`：refcount + 两阶段 GC
- [ ] `migrations/011_artifacts.sql`
- [ ] tests/unit/service/artifact/test_dedup_hash.py
- [ ] tests/unit/service/artifact/test_refcount.py
- [ ] tests/unit/service/artifact/test_summarize_text.py
- [ ] tests/unit/service/artifact/test_summarize_pdf.py
- [ ] tests/unit/service/artifact/test_gc_two_phase.py

### 2.3 EmbeddingGateway

- [ ] `src/service/embedding/protocol.py`：EmbeddingProvider
- [ ] `src/service/embedding/openai_provider.py`
- [ ] `src/service/embedding/bge_provider.py`
- [ ] `src/service/embedding/gateway.py`：缓存 + 批处理
- [ ] tests/unit/service/embedding/test_cache_hit.py
- [ ] tests/unit/service/embedding/test_batch.py
- [ ] tests/unit/service/embedding/test_dim_validation.py

### 2.4 KBService + Worker

- [ ] `src/service/kb/service.py`：register_doc / list_docs / delete
- [ ] `src/service/kb/chunker.py`：heading-aware + recursive
- [ ] `src/service/kb/loader.py`：text / markdown / pdf / html
- [ ] `src/service/kb/ingest_worker.py`：LISTEN/NOTIFY 驱动
- [ ] `migrations/012_kb.sql`
- [ ] tests/unit/service/kb/test_chunker_heading.py
- [ ] tests/unit/service/kb/test_chunker_recursive.py
- [ ] tests/unit/service/kb/test_loader_pdf.py
- [ ] tests/unit/service/kb/test_ingest_worker.py
- [ ] tests/integration/kb/test_e2e_ingest.py（注册 → ready）

### 2.5 MemoryService

- [ ] `src/service/memory/service.py`：remember/forget/recall/list_
- [ ] `src/service/memory/extractor.py`：从 session summary 抽候选
- [ ] `src/service/memory/dedup.py`：vector similarity 去重
- [ ] `src/service/memory/conflict.py`：事实型记忆冲突检测
- [ ] `src/service/memory/worker.py`：后台压缩
- [ ] `migrations/013_memory.sql`
- [ ] tests/unit/service/memory/test_remember_dedup.py
- [ ] tests/unit/service/memory/test_recall_mmr.py
- [ ] tests/unit/service/memory/test_recall_recency_boost.py
- [ ] tests/unit/service/memory/test_conflict_resolution.py
- [ ] tests/unit/service/memory/test_extractor.py

### 2.6 Retriever

- [ ] `src/service/retriever.py`：unify KB + Memory,hybrid + MMR
- [ ] tests/unit/service/retriever/test_hybrid_fusion.py
- [ ] tests/unit/service/retriever/test_mmr_diversity.py
- [ ] tests/unit/service/retriever/test_cross_channel_dedup.py

### 2.7 ServiceContainer

- [ ] `src/service/__init__.py`：`build_services(infra, config) -> Services`
- [ ] tests/integration/service/test_container.py

**Phase 2 验收**：
- 所有单元 + 集成测试通过
- 跑通 CLI demo：`python -m src.service.demo` 模拟存一段对话 → 召回 → 显示

---

## Phase 3 · Agent 编排层

**目标**：ReAct loop 跑得起来,无 Gateway 也能用 CLI 验证完整 Agent。

### 3.1 LLMGateway

- [ ] `src/agent/llm_router.py`：catalog 加载 + resolve
- [ ] `src/agent/llm_circuit_breaker.py`
- [ ] `src/agent/llm_providers/base.py`：Protocol
- [ ] `src/agent/llm_providers/openai.py`
- [ ] `src/agent/llm_providers/anthropic.py`
- [ ] `src/agent/llm_providers/dashscope.py`（OpenAI compatible 复用）
- [ ] `src/agent/llm_providers/doubao.py`
- [ ] `src/agent/llm_gateway.py`：retry + fallback + cancel
- [ ] tests/unit/agent/llm/test_router_resolve.py
- [ ] tests/unit/agent/llm/test_circuit_breaker.py
- [ ] tests/unit/agent/llm/test_first_chunk_retry.py
- [ ] tests/unit/agent/llm/test_mid_stream_no_retry.py
- [ ] tests/unit/agent/llm/test_fallback_chain.py
- [ ] tests/unit/agent/llm/test_cancel_releases.py
- [ ] tests/unit/agent/llm/providers/test_openai_chunks.py
- [ ] tests/unit/agent/llm/providers/test_anthropic_chunks.py

### 3.2 ContextBuilder

- [ ] `src/agent/context_builder.py`
- [ ] `src/agent/token_estimator.py`
- [ ] tests/unit/agent/context/test_budget_allocation.py
- [ ] tests/unit/agent/context/test_role_render.py
- [ ] tests/unit/agent/context/test_manifest_filter.py
- [ ] tests/unit/agent/context/test_memory_truncate.py
- [ ] tests/unit/agent/context/test_kb_render.py
- [ ] tests/unit/agent/context/test_history_window.py
- [ ] tests/unit/agent/context/test_assemble_drop.py
- [ ] tests/unit/agent/context/test_hard_truncate_pair_safe.py
- [ ] tests/unit/agent/context/test_token_estimator.py

### 3.3 ToolRegistry & Builtin Tools

- [ ] `src/tools/__init__.py`：Tool Protocol
- [ ] `src/tools/registry.py`：load_builtins / load_from_dir
- [ ] `src/tools/builtin/final_answer.py`
- [ ] `src/tools/builtin/delegate_task.py`
- [ ] `src/tools/builtin/load_skill.py`
- [ ] `src/tools/builtin/read_artifact.py`
- [ ] `src/tools/builtin/remember.py`
- [ ] `src/tools/builtin/forget.py`
- [ ] tests/unit/tools/test_registry_load.py
- [ ] tests/unit/tools/builtin/test_final_answer.py
- [ ] tests/unit/tools/builtin/test_remember.py
- [ ] tests/unit/tools/builtin/test_read_artifact.py

### 3.4 ToolExecutor

- [ ] `src/agent/tool_executor.py`：8 步 pipeline
- [ ] tests/unit/agent/executor/test_permission_deny.py
- [ ] tests/unit/agent/executor/test_schema_invalid.py
- [ ] tests/unit/agent/executor/test_timeout.py
- [ ] tests/unit/agent/executor/test_cancel_propagation.py
- [ ] tests/unit/agent/executor/test_parallel_semaphore.py
- [ ] tests/unit/agent/executor/test_idempotent_cache.py
- [ ] tests/unit/agent/executor/test_artifact_fallback.py
- [ ] tests/unit/agent/executor/test_failure_isolation.py
- [ ] tests/unit/agent/executor/test_event_publishing.py

### 3.5 RunRegistry

- [ ] `src/runtime/run_registry.py`
- [ ] `migrations/020_runs.sql`：runs + run_closure
- [ ] tests/unit/runtime/registry/test_register_self_closure.py
- [ ] tests/unit/runtime/registry/test_link_parent_chain.py
- [ ] tests/unit/runtime/registry/test_descendants.py
- [ ] tests/unit/runtime/registry/test_cancel_cascade.py
- [ ] tests/unit/runtime/registry/test_state_transitions.py

### 3.6 AgentRegistry

- [ ] `src/runtime/agent_registry.py`：YAML 加载 + mtime 热更
- [ ] `agents/default.yaml`、`agents/research-agent.yaml` 示例
- [ ] tests/unit/runtime/test_agent_registry_load.py
- [ ] tests/unit/runtime/test_agent_registry_hot_reload.py

### 3.7 BaseAgent + Orchestrator + Runtime

- [ ] `src/agent/base_agent.py`：ReAct loop + _stream_llm + _check_termination
- [ ] `src/runtime/orchestrator.py`：run + spawn_subagent
- [ ] `src/runtime/runtime.py`：initialize / start_run / shutdown / workers
- [ ] `src/runtime/cli.py`：命令行端到端测试入口
- [ ] tests/unit/agent/base_agent/test_run_loop_terminate.py
- [ ] tests/unit/agent/base_agent/test_step_check_budget.py
- [ ] tests/unit/agent/base_agent/test_tool_call_dispatch.py
- [ ] tests/unit/agent/base_agent/test_stream_event_publish.py
- [ ] tests/unit/runtime/test_orchestrator_spawn_subagent.py
- [ ] tests/unit/runtime/test_runtime_lifecycle.py
- [ ] tests/integration/agent/test_react_full_loop.py（mock LLM + tools）

**Phase 3 验收**：
- `python -m src.runtime.cli --agent default --message "你好"` 能跑通完整 ReAct,看到流式输出
- 取消测试：跑一个慢 SubAgent,cancel 后所有后代立刻终止

---

## Phase 4 · Gateway 层

**目标**：HTTP API + SSE 接入,浏览器/curl 可直接试用。

### 4.1 路由 + Schemas

- [ ] `src/gateway/schemas.py`：所有 Pydantic in/out
- [ ] `src/gateway/deps.py`：auth / runtime / trace
- [ ] `src/gateway/errors.py`：异常映射
- [ ] `src/gateway/sse.py`：SSE 适配器 + heartbeat
- [ ] `src/gateway/routes/sessions.py`
- [ ] `src/gateway/routes/runs.py`
- [ ] `src/gateway/routes/artifacts.py`
- [ ] `src/gateway/routes/memory.py`
- [ ] `src/gateway/routes/kb.py`
- [ ] `src/gateway/routes/agents.py`
- [ ] `src/gateway/app.py`：装配 + lifespan + middleware

### 4.2 中间件

- [ ] `src/gateway/middleware/trace_id.py`
- [ ] `src/gateway/middleware/access_log.py`
- [ ] `src/gateway/middleware/rate_limit.py`（可选,先跳过）

### 4.3 测试

- [ ] tests/unit/gateway/test_auth.py
- [ ] tests/unit/gateway/test_error_mapping.py
- [ ] tests/unit/gateway/test_sse_format.py
- [ ] tests/unit/gateway/test_sse_replay.py
- [ ] tests/integration/gateway/test_create_session_run.py
- [ ] tests/integration/gateway/test_run_cancel.py
- [ ] tests/integration/gateway/test_artifact_upload_dl.py
- [ ] tests/integration/gateway/test_kb_ingest_e2e.py
- [ ] tests/integration/gateway/test_concurrent_runs.py
- [ ] tests/integration/gateway/test_sse_reconnect_resume.py

**Phase 4 验收**：
- `uvicorn src.gateway.app:app` 启动
- curl 完整跑通：建 session → start_run → SSE 消费 → 看到流式回答
- 浏览器 EventSource 能消费 SSE 流

---

## Phase 5 · Tools / Skills 持续扩充

**目标**：补足真实业务工具,让助理实用。

### 5.1 通用工具

- [ ] `src/tools/web_search.py`（Bing / SerpAPI / Tavily）
- [ ] `src/tools/web_fetch.py`（trafilatura 抽正文）
- [ ] `src/tools/python_exec.py`（沙箱执行,风险控制）
- [ ] `src/tools/datetime.py`、`src/tools/calculator.py` 等小工具

### 5.2 飞书工具集

- [ ] `src/tools/lark/auth.py`：tenant_access_token 缓存
- [ ] `src/tools/lark/im.py`：发消息
- [ ] `src/tools/lark/calendar.py`：建/查日程
- [ ] `src/tools/lark/docs.py`：建文档
- [ ] `src/tools/lark/sheet.py`：表格操作
- [ ] `src/tools/lark/bitable.py`：多维表格

### 5.3 Skills

- [ ] `src/skills/research.md`（research-agent 的 system prompt）
- [ ] `src/skills/code_review.md`
- [ ] `src/skills/email_draft.md`
- [ ] 加载机制走 `load_skill` 工具,不需要单独代码

### 5.4 SubAgent

- [ ] `src/agents/research-agent.yaml`
- [ ] `src/agents/code-agent.yaml`
- [ ] tests/integration/agent/test_research_subagent.py

---

## 横切关注点（每个 Phase 都要注意）

### 日志

- [ ] `src/common/logging.py`：structlog 配置
- [ ] 每条日志带 `run_id, session_id, user_id, trace_id` contextvars 绑定

### Metrics

- [ ] 预留 `src/common/metrics.py` 接口（先 noop）
- [ ] 后续接 Prometheus client

### 配置

- [ ] `config/dev.yaml`：本地开发（fs 替 OSS、in-memory cache 替 Redis）
- [ ] `config/prod.yaml`：阿里云 RDS + OSS + Redis（用环境变量注 secret）
- [ ] `.env.example` 列出所有需要的环境变量

### CI/CD

- [ ] GitHub Actions / 阿里云效流水线：lint + test + build image
- [ ] 集成测试用 docker-compose 起 PG/Redis

---

## 测试金字塔目标

```
         /────────\
        / E2E (5%)\          ← Phase 4 后补：真实跑 docker-compose 全栈
       /───────────\
      / Integration\         ← 每个 Phase 都有,约 20%
     /     (20%)    \
    /─────────────────\
   /     Unit (75%)    \    ← 每个模块必备,纯内存 / SQLite mock
  /─────────────────────\
```

- 单测目标覆盖率 ≥ 80%
- 关键路径（ReAct loop、cancel cascade、SSE replay）必须 100%

---

## 里程碑回顾点

每完成一个 Phase 必须：
1. 全部测试通过 + ruff/mypy 干净
2. 写一段简短的 demo 脚本（CLI 或 curl）证明可独立运行
3. 在 README 添加该阶段的"如何跑通"说明
4. 把发现的设计漏洞回写到对应 `XX-*.md` 文档

---

## 风险与备选

| 风险 | 触发表现 | 备选方案 |
|---|---|---|
| pgvector 性能不足 | recall p99 > 1s | 切 Milvus / Qdrant 单机 |
| LISTEN/NOTIFY 在云 RDS 受限 | NOTIFY 不到 | 改 Redis Streams |
| OSS 直连慢 | 上传/下载延迟 | 加 CDN 或本地缓存层 |
| 单进程 GIL 瓶颈 | CPU 100% | 拆 Worker 进程跑 ingest/memory |
| 流式 cancel 不及时 | 模型继续输出 | provider 层强制 close stream |

---

## 下一步

按 Phase 0 → 5 顺序推进。每个 Phase 内部按本文档列表逐项打勾,全部勾完再进下一个 Phase。

如果时间紧,**最小可用切片**：
- Phase 0 ✅
- Phase 1：只做 PG + 内存 cache + InProcessEventBus（跳过 OSS、Redis）
- Phase 2：只做 SessionService + EmbeddingGateway（跳过 KB / Memory）
- Phase 3：跑通 ReAct loop（先不做 SubAgent / 缓存）
- Phase 4：只做 POST /runs + GET /events
- 总计 ≈ 1 周可见到端到端流式回答

之后逐步把跳过的部分补齐。