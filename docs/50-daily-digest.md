# 50 · 每日技术日报

> Agent-driven 定时任务：Scheduler 启动 Agent Run → Agent 调用 Tool 抓取 → LLM 摘要 → 存 Artifact

---

## 1. 架构

```
Scheduler (每天 8:00 或手动触发)
    │
    ▼
Runtime.start_run(
    agent="daily-digest",
    message="生成今日技术日报"
)
    │
    ▼
daily-digest Agent (ReAct Loop)
    │
    ├── 加载 daily-digest Skill (日报指令 + 数据源列表)
    │   └── load_skill 自动激活 skill 声明的 custom tools
    │
    ├── rss_fetch(urls=[...])     ← 并发抓 RSS（数据直接返回在 content）
    ├── hackernews_top(limit=10)  ← HN 热门
    ├── github_trending(limit=10) ← GitHub Trending
    └── artifact_save(name, content) ← 保存日报
```

**Custom Tool 加载方式**：启动时预初始化所有 custom tools 到 `_custom`（不激活），当 skill 被 `load_skill` 加载时，根据 skill 的 `required_tools` / `optional_tools` 自动激活到 registry。

### 手动触发（调试）

```
方式1: CLI
  python -m src.runtime.cli --agent daily-digest --message "生成今日技术日报"

方式2: API
  POST /api/chat {"agent": "daily-digest", "message": "生成今日技术日报"}

方式3: 对话中
  用户: "帮我生成今天的日报"
  → delegate_task → daily-digest agent
```

---

## 2. 新增文件

```
src/tools/custom/                  ← 启动时统一加载到 _tools，builtin 常驻 + allowed 过滤
├── __init__.py
├── rss_fetch.py                   ← RSS/Atom 解析
├── hackernews.py                  ← HN API
├── github_trending.py             ← GitHub Trending
└── artifact_save.py               ← 写入 Artifact

src/skills/
└── daily-digest.md                ← 日报 Skill (声明 required_tools)

src/agents/
└── daily-digest.yaml              ← 日报 Agent

src/runtime/
└── scheduler.py                   ← 调度器
```

### 修改文件

```
src/tools/registry.py              ← 全量加载 + list_for_agent(builtin + allowed + extra)
src/tools/builtin/load_skill.py    ← 加载 skill 时注入 active_custom_tools
src/agent/tool_executor.py         ← _check_permission builtin 放行 + active_custom_tools
src/agent/context_builder.py       ← _render_tools/_build_manifest 传递 extra_tools
src/agent/orchestrator.py          ← _spawn_by_skills 保留激活的 custom tools
src/runtime/runtime.py             ← 启动时 load_from_dir("src/tools/custom")
src/common/config.py               ← SchedulerConfig
config/dev.yaml                    ← scheduler 配置
pyproject.toml                     ← feedparser, croniter
```

---

## 3. Tool 设计

### 3.1 rss_fetch

| | |
|---|---|
| 输入 | `urls: list[str]`, `limit: int = 20` |
| 输出 | `[{title, url, author, summary, published_ts, source_url}]` |
| 依赖 | `feedparser`, `httpx` |
| 容错 | 单个 URL 超时/失败不阻塞其他，返回部分结果 + 错误列表 |

### 3.2 hackernews_top

| | |
|---|---|
| 输入 | `limit: int = 15`, `min_score: int = 50` |
| 输出 | `[{title, url, score, descendants, author}]` |
| 依赖 | Firebase API: `hacker-news.firebaseio.com/v0` |

### 3.3 github_trending

| | |
|---|---|
| 输入 | `limit: int = 10`, `language: str = ""`, `since: str = "daily"` |
| 输出 | `[{full_name, html_url, description, language, stargazers_count, stars_today}]` |
| 依赖 | 爬取 `https://github.com/trending` 页面，解析 article block |

### 3.4 artifact_save

| | |
|---|---|
| 输入 | `name: str`, `content: str`, `content_type: str = "text/markdown"` |
| 输出 | `{artifact_id, name}` |
| 依赖 | `ctx.services.artifact` |

---

## 4. Skill 设计

```yaml
# daily-digest.md frontmatter
name: daily-digest
required_tools: []
optional_tools: [rss_fetch, hackernews_top, github_trending, artifact_save]
agent_profile:
  temperature: 0.3
  max_steps: 15
  token_budget: 32000
  deadline_sec: 300
```

指令正文：
- 15 个 RSS 源 URL 列表
- 工作流：加载 skill（自动激活 custom tools）→ 并发抓取 → 去重 → 分类摘要 → 保存
- 输出格式：Markdown 模板

---

## 5. Agent 配置

```yaml
# daily-digest.yaml
name: daily-digest
system_prompt: 你是技术日报编辑
preferred_model: deepseek-chat
temperature: 0.3
allowed_tools:
  - load_skill
  - final_answer
  - rss_fetch
  - hackernews_top
  - github_trending
  - artifact_save
denied_tools:
  - web_fetch
  - web_search
  - bash_exec
  - python_exec
  - delegate_task
  - datetime
  - read_artifact
allowed_skills:
  - daily-digest
max_steps: 8
token_budget: 300000
deadline_sec: 600
max_parallel_tools: 5
```

---

## 6. 数据源（15 RSS + 2 聚合）

1. OpenAI — `https://openai.com/blog/rss.xml`
2. Anthropic — `https://www.anthropic.com/research/feed`
3. DeepMind — `https://deepmind.google/blog/feed`
4. Meta AI — `https://ai.meta.com/blog/feed`
5. Simon Willison — `https://simonwillison.net/atom/everything/`
6. Cloudflare — `https://blog.cloudflare.com/rss`
7. Netflix TechBlog — `https://netflixtechblog.com/feed`
8. Stripe Engineering — `https://stripe.com/blog/engineering/feed`
9. Slack Engineering — `https://slack.engineering/feed`
10. Figma Engineering — `https://www.figma.com/blog/engineering/feed`
11. GitHub Engineering — `https://github.blog/engineering/feed`
12. Spotify Engineering — `https://engineering.atspotify.com/feed`
13. Databricks — `https://www.databricks.com/blog/feed`
14. Uber Engineering — `https://www.uber.com/blog/engineering/feed`
15. AWS ML — `https://aws.amazon.com/blogs/machine-learning/feed`
- Hacker News — Top 15 (Firebase API)
- GitHub Trending — 爬取 Trending 页面 daily Top 10

---

## 7. 实现阶段

### Phase 1: Custom Tool 基础 ✅

- [x] `artifact_save.py` — 包装 ArtifactStore.save()，28 单测
- [x] `rss_fetch.py` — 并发抓 RSS/Atom，单源失败不阻塞，published_at 输出 ISO 8601
- [x] `hackernews.py` — HN Firebase 公开 API，min_score 过滤
- [x] `github_trending.py` — 爬取 github.com/trending 页面，解析 star 增速，过滤 sponsor 卡片
- [x] 依赖：`feedparser>=6.0` 加入 pyproject.toml

### Phase 2: 统一加载 + 三层过滤 ✅

- [x] 移除 `load_custom_tools` builtin tool
- [x] `ToolRegistry` 启动时全部加载（builtin + custom 都进 `_tools`）
- [x] `list_for_agent(spec, extra_tools)` — builtin 常驻 + allowed_tools + extra_tools
- [x] `load_skill` 将 skill 声明的 tools 注入 `ctx.extra_inputs["active_custom_tools"]`
- [x] `_check_permission` — builtin 放行，custom 查 `allowed_tools ∪ active_custom_tools`
- [x] `_render_tools` / `_build_manifest` 传递 `extra_tools`
- [x] `_spawn_by_skills` 保留父 agent 已激活的 custom tools
- [x] 单测：36 registry + custom tool 测试

### Phase 3: Skill + Agent ✅

- [x] `src/skills/daily-digest.md` — 15 RSS 源 + HN + GitHub Trending，工作流指令，输出模板
- [x] `src/agents/daily-digest.yaml` — Agent 配置，allowed_tools 含 custom tools + load_skill
- [x] Skill 加载验证：required_tools / optional_tools / agent_profile 正确解析
- [x] Agent 解析验证：allowed_tools / allowed_skills / max_steps 正确加载
- [x] 集成测试：手动 start_run，验证完整链路

### Phase 4: Scheduler ✅

- [x] `src/runtime/scheduler.py` — DailyDigestScheduler，croniter 解析 + 追补 + launchd
- [x] `src/common/config.py` — SchedulerConfig Pydantic 模型
- [x] `config/dev.yaml` — scheduler 配置块（enabled, cron, agent 等）
- [x] Runtime 集成 — `initialize()` 启动 scheduler，`shutdown()` 停止
- [x] 单测：11 个（cron 解析、next/prev 计算、追补、手动触发、disabled、stop）
- [x] `com.poppy.daily-digest.plist` — macOS launchd 配置文件
- [x] 引入 `croniter>=6.0` 依赖

### 部署 launchd

```bash
# 安装（macOS 开机自启）
cp com.poppy.daily-digest.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.poppy.daily-digest.plist

# 检查状态
launchctl list | grep poppy

# 卸载
launchctl unload ~/Library/LaunchAgents/com.poppy.daily-digest.plist
```

调度策略：
- **launchd** 每天 8:00 触发 CLI 生成日报（用于 headless 模式，电脑开着时）
- **进程内 Scheduler** 每天 8:00 通过 croniter 触发（用于 Gateway 运行时）
- **二者互斥**：launchd 和进程内 scheduler 不要同时启用，否则会重复生成
- Scheduler 启动时追补：如果今天的触发时间已过，自动补触发一次
- 手动触发：`python -m src.runtime.cli --agent daily-digest --message "..."`

---

## 8. 新增依赖

```toml
"feedparser>=6.0",    # RSS/Atom 解析
"croniter>=6.0",      # Cron 表达式解析
```
