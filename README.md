<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/Poppy-FF6B35?style=for-the-badge&logo=poetry&logoColor=white">
    <img alt="Poppy" src="https://img.shields.io/badge/Poppy-FF6B35?style=for-the-badge&logo=poetry&logoColor=white">
  </picture>
</p>

<p align="center">
  <strong>个人 AI 助理 — 记忆、工具、多 Agent 协作，端到端可控</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/tests-398_passed-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/ruff-clean-000" alt="Lint">
  <img src="https://img.shields.io/badge/mypy-strict-blue" alt="Type Check">
</p>

---

## 这是什么？

Poppy 是一个**单租户个人 AI 助理**。它能：

- 💬 **多轮对话** — 流式 ReAct 循环，实时输出
- 🧠 **长期记忆** — 自动提取用户偏好与事实，持久化到知识库
- 🔧 **工具调用** — 14 个内建工具，支持 shell 执行、Python 沙箱、网络搜索等
- 🤝 **多 Agent 协作** — 主 Agent 派发子 Agent，深度/并发可控
- 📚 **私域检索** — 用户文档/网页/笔记的向量+关键词混合检索
- 🎛️ **Skill 热加载** — Markdown 格式的技能文件，安装即用，用户可覆盖内建

---

## 架构总览

```
                       ┌──────────────────────────┐
                       │   Client (Web / CLI)     │
                       └──────────┬───────────────┘
                  HTTP POST /runs │ SSE /events
                                  │
              ┌───────────────────┴────────────────────┐
              │       Layer 6 · Gateway (FastAPI)       │
              │   Auth │ RateLimit │ SSE │ Middleware    │
              └───────────────────┬────────────────────┘
                                  │
              ┌───────────────────┴────────────────────┐
              │       Layer 5 · Runtime (Singleton)     │
              │   AgentRegistry │ EventBus │ RunRegistry│
              └───────────────────┬────────────────────┘
                                  │
              ┌───────────────────┴────────────────────┐
              │     Layer 4 · Orchestrator (per-Run)    │
              └───────────────────┬────────────────────┘
                                  │
              ┌───────────────────┴────────────────────┐
              │       Layer 3 · BaseAgent (ReAct)       │
              │   perceive → plan → act → observe       │
              │   ┌──────────┐  ┌───────────────────┐   │
              │   │ LLMGateway│  │  ToolExecutor     │   │
              │   └──────────┘  └───────────────────┘   │
              └───────────────────┬────────────────────┘
                                  │
              ┌───────────────────┴────────────────────┐
              │        Layer 1-2 · Service + Infra      │
              │   Session │ Memory │ KB │ Retriever     │
              │   PG │ Redis │ OSS │ Vector │ FTS5     │
              └────────────────────────────────────────┘
```

核心循环是 **ReAct**：Agent 感知上下文 → 规划下一步 → 调用 LLM 流式输出 → 执行工具 → 观察结果，循环直到 `final_answer`。

更多细节见 **[架构设计文档](docs/ARCHITECTURE.md)** 和 **[分层设计索引](docs/00-overview.md)**。

---

## 快速开始

```bash
# 1. 克隆并安装
git clone <repo-url> && cd poppy
pip install -e .[dev]

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，至少填写 DEEPSEEK_API_KEY

# 3. 初始化数据库
python -m src.infra.relational.migrator config/dev.yaml

# 4. 命令行对话
python -m src.runtime.cli --agent default --message "你好"

# 5. 启动 Web 聊天界面
python -m src.gateway.app
# 访问 http://localhost:8000/chat
```

---

## 每日技术日报

Poppy 内置了一个 `daily-digest` Agent，每天早上 8:00（北京时间）自动生成技术日报：

- **数据源**：15 个技术博客 RSS（OpenAI、Anthropic、DeepMind、Cloudflare 等）+ Hacker News + GitHub Trending
- **自动分类**：LLM 将文章归入 AI/LLM、基础设施、开源项目、前端/工具等类别，每类 3-5 条
- **持久化**：日报保存为 Markdown Artifact，可通过 `/digest` 页面随时查阅
- **定时调度**：基于 launchd（macOS）或 cron，支持开机追补（错过 8:00 自动补跑）

```bash
# 手动生成日报
python -m src.runtime.cli --agent daily-digest --message "生成今日技术日报"

# 查看日报列表
open http://localhost:8000/digest
```

---

## 内建工具

Poppy 出厂自带 18 个工具，覆盖日常 AI 助理场景：

| 工具 | 用途 | 特色 |
|---|---|---|
| `bash_exec` | 执行 bash 命令 | 安全沙箱，危险命令拦截，30s 超时 |
| `python_exec` | Python 代码执行 | AST 安全沙箱，禁止文件/网络访问 |
| `web_search` | 互联网搜索 | Tavily API，支持 advanced 深度搜索 |
| `web_fetch` | 抓取网页内容 | HTML 自动清洗为纯文本，8KB 上限 |
| `calculator` | 数学计算 | 安全 AST 求值，支持三角函数/对数 |
| `datetime` | 日期时间 | 时区转换、时间差计算 |
| `final_answer` | 终止本轮对话 | 干净结束 ReAct 循环 |
| `delegate_task` | 派发子 Agent | 并发/深度限制，预算隔离 |
| `load_skill` | 加载技能 | 动态切换 Agent 行为模式 |
| `list_skills` | 列出所有技能 | 支持按来源过滤 (builtin/user) |
| `skill_install` | 安装技能 | 从 URL 下载 .md 技能文件 |
| `read_artifact` | 读取大文件 | 返回 artifact 内容 |
| `remember` | 保存记忆 | 写入长期记忆库 |
| `forget` | 删除记忆 | 从记忆库中移除 |

### Custom 工具（Skill 按需加载）

| 工具 | 用途 | 所属 Skill |
|---|---|---|
| `rss_fetch` | 并发抓取 RSS/Atom 源 | daily-digest |
| `hackernews_top` | Hacker News 热门文章 | daily-digest |
| `github_trending` | GitHub Trending 仓库 | daily-digest |
| `artifact_save` | 保存内容为 Artifact | daily-digest |

---

## Skill 系统

Skill 是以 **Markdown 文件** 定义的 Agent 行为扩展。双路径加载：

```
src/skills/           ← 内建技能（随项目发布）
src/skills-user/      ← 用户技能（你安装的，同名覆盖内建）
```

**内建 Skill**：

| Skill | 用途 |
|---|---|
| `daily-digest` | 每日技术日报，从 RSS/HN/GitHub 抓取并分类摘要 |

**安装方式**：通过聊天界面让 Agent 执行 `skill_install` 工具，指定 URL 即可。

```
用户: 帮我安装 skill-creator 技能
Agent: → 自动下载 skill-creator.md → 写入 src/skills-user/ → 安装完成
```

---

## 项目结构

```
poppy/
├── config/                # 环境配置 (dev.yaml / prod.yaml)
├── docs/                  # 架构设计文档 (ARCHITECTURE.md + 系列)
├── migrations/            # PostgreSQL 迁移脚本
├── src/
│   ├── common/            # 共享类型 (types.py)、错误、时钟、ID 生成
│   ├── infra/             # 基础设施 — PG/Redis/OSS/向量/事件总线
│   ├── service/           # 业务服务 — Session/Memory/KB/Embedding
│   ├── agent/             # Agent 核心 — ReAct/LLM Gateway/Tool Executor
│   ├── runtime/           # 运行时 — Orchestrator/RunRegistry/AgentRegistry/CLI
│   ├── gateway/           # HTTP 层 — FastAPI/SSE/Routes
│   ├── tools/             # 工具协议 + 14 个内建工具
│   ├── skills/            # 内建 Skill 定义 (.md)
│   └── agents/            # AgentSpec YAML 配置
├── tests/
│   ├── unit/              # 单元测试
│   └── integration/       # 集成测试
└── pyproject.toml
```

---

## 技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python 3.11+ (full async, strict typing) |
| Web 框架 | FastAPI + Uvicorn |
| 流式推送 | Server-Sent Events (SSE) |
| LLM 接入 | OpenAI-compatible (DeepSeek/豆包/通义统一适配) |
| 主数据库 | PostgreSQL + pgvector (向量) + pg_jieba (全文) |
| 开发数据库 | SQLite + FTS5 + sqlite-vec |
| 缓存 | Redis (prod) / MemoryCache (dev) |
| 对象存储 | 阿里云 OSS (prod) / 本地文件 (dev) |
| 搜索 API | Tavily |

---

## 设计哲学

- **分层清晰** — 六层隔离，职责单一，依赖单向
- **事件驱动** — 跨模块通信全部走 EventBus，SSE 透明转发
- **per-Run 隔离** — 每个请求独立的 AgentContext，预算和取消信号隔开
- **延迟绑定** — AgentSpec 静态配置，运行时构造 Context，Tool/Skill 动态加载
- **先跑通再优化** — Infra 用 SQLite 即可开发，换 PG 只需改配置

---

## 开发

```bash
# 运行所有测试
pytest tests/ -v

# 类型检查
mypy src/

# Lint
ruff check src/ tests/
ruff format src/ tests/ --check

# 启动开发服务
PYTHONPATH=. python -m src.gateway.app
```

---

**[→ 完整架构文档](docs/ARCHITECTURE.md)** &nbsp;|&nbsp; **[→ 设计文档索引](docs/00-overview.md)**
