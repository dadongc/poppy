# Personal AI Assistant —— 架构设计与实施文档

> **目标读者**：架构师、后端工程师、代码生成模型（Claude / Codex）
> **目标用途**：作为项目的 Single Source of Truth，指导从零到一的代码实现
> **更新策略**：每个模块代码落地后，必须回头校准对应文档；文档与代码不一致 = 文档错

---

## 文档索引

按"先读全局，再读细节"的顺序编排。

### 一、全局篇

| 文档 | 内容 | 何时读 |
|---|---|---|
| 00-overview.md | 整体架构图、分层拓扑、端到端时序、能力清单、设计哲学 | 所有人入门必读 |
| 01-common-types.md | 跨模块共享数据结构（AgentContext / AgentSpec / Event / Artifact / ToolResult / LLMChunk 等） | 写任何模块前都要读 |

### 二、Infra 层（Phase 1）

| 文档 | 内容 |
|---|---|
| 10-infra.md | Protocol 接口契约、PG/Redis/OSS 实现、Schema 演进、Jobs 队列 |

### 三、Service 层（Phase 2）

| 文档 | 内容 |
|---|---|
| 20-service.md | SessionService / MemoryService / ArtifactStore / KB / Retriever / EmbeddingGateway |

### 四、Agent 编排层（Phase 3）

| 文档 | 内容 |
|---|---|
| 30-agent-runtime.md | Runtime / Orchestrator / BaseAgent / SubAgent / AgentRegistry |
| 31-context-builder.md | ContextBuilder（7 段 prompt 组装 + 预算裁剪） |
| 32-tool-executor.md | ToolExecutor（8 步 pipeline + 内建工具） |
| 33-llm-gateway.md | LLMGateway（统一 chunk 协议 + ModelRouter + retry/fallback） |
| 34-eventbus-runregistry.md | EventBus + RunRegistry（状态机 + 闭包表） |

### 五、Gateway 层（Phase 4）

| 文档 | 内容 |
|---|---|
| 40-gateway.md | FastAPI HTTP API、SSE 推送适配、鉴权、上传、生命周期 |

### 六、实施篇

| 文档 | 内容 |
|---|---|
| 90-roadmap.md | **逐模块 TODO + 单测清单**（按 Phase 0~4 自底向上） |

---

## 项目目录结构（最终态）

```
personal-assistant/
├── pyproject.toml
├── README.md
├── config/
│   ├── default.yaml
│   ├── dev.yaml
│   └── prod.yaml
├── docs/                          # 本目录所有 .md
├── data/                          # 本地开发用
│   ├── sqlite/
│   └── artifacts/
├── migrations/                    # PG schema 迁移
├── src/
│   ├── common/                    # 共享类型与工具
│   ├── infra/                     # Phase 1
│   ├── service/                   # Phase 2
│   ├── agent/                     # Phase 3
│   ├── runtime/                   # Phase 3
│   ├── gateway/                   # Phase 4
│   ├── tools/                     # 业务工具
│   ├── skills/                    # SKILL.md 集合
│   └── agents/                    # AgentSpec 配置
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── cli/
    └── main.py
```

---

## Phase 划分

| Phase | 内容 | 验收 |
|---|---|---|
| **0** | 项目骨架 + 共享类型 | `pytest tests/unit/common` 全绿 |
| **1** | Infra 层 | 单元测试 + 真实 PG/Redis/OSS 集成测试通过 |
| **2** | Service 层 | 单测 + 集成测试通过 |
| **3** | Agent 编排层 | CLI 模式下能跑通完整 ReAct loop |
| **4** | Gateway 层 | HTTP + SSE 端到端 e2e 测试通过 |
| **5** | 内建 Tools / Skills | 飞书集成、Web 搜索等业务能力上线 |

---

## 设计哲学

1. **分层清晰**：六层（Gateway / Runtime / Orchestrator / Agent / Service / Infra）职责单一
2. **事件驱动**：跨模块通信走 EventBus，强解耦
3. **协议统一**：核心数据结构跨模块复用
4. **per-Run 隔离**：AgentContext 携带运行时状态，SubAgent 隔离预算和取消
5. **延迟绑定**：AgentSpec 静态、AgentContext 运行时构造，Tool/Skill 动态加载
6. **存储统一**：PG 一库三用（关系+向量+全文），Redis 多用途，OSS 单一职责
7. **异步友好**：全 asyncio + 流式 + 后台 Worker
8. **演进路径明确**：用最简方案撑住单实例，有瓶颈再升级

---

## 编码约定

- **Python 版本**：3.11+
- **类型提示**：所有公开 API 必须带类型；`from __future__ import annotations` 默认开启
- **数据结构**：优先用 `@dataclass(slots=True, kw_only=True)`；只有 API 边界用 Pydantic
- **异步**：业务层全 async；同步代码只允许在纯 CPU 计算函数
- **依赖注入**：通过 `Runtime` 单例 + 构造函数注入，禁止全局变量
- **日志**：`structlog`，结构化字段；禁止 print
- **测试**：`pytest` + `pytest-asyncio`
- **格式化**：`ruff` + `ruff format`；行宽 100