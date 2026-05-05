# 31 · ContextBuilder（上下文组装器）

> 把"分散在各 Service 的状态"组装成一段**LLM 可消费的 prompt**。这是 Agent 的"短期记忆"装配工厂。

实现位置：`src/agent/context_builder.py`

---

## 1. 设计目标

- **确定性优先**：每轮组装规则固定,不让 LLM 自主决定带什么上下文（避免不稳定 + token 漂移）
- **token 预算可控**：每段有固额,超额按优先级裁剪,可观测每段实际占用
- **可观测**：输出 `PromptPayload.sections` 明细,方便事后回放与调优
- **provider 中立**：输出 `messages: list[dict]` + `tools: list[dict]`,由 LLMGateway 转 provider-specific 格式

---

## 2. 七段式结构

```
┌────────────────────────────────────────────────────────────┐
│ 1. role        系统指令（agent.system_prompt + 全局指令）   │ ← 固定
│ 2. manifest    工具/技能清单（仅当前可用）                  │ ← 固定
│ 3. env         运行环境（时间、用户、平台、当前 session）   │ ← 固定
│ 4. memory      召回的长期记忆条目                           │ ← 可裁
│ 5. kb          召回的知识库片段（带引用）                    │ ← 可裁
│ 6. history     近 N 轮对话（含工具消息）                     │ ← 可压缩
│ 7. current     当前用户消息 + ReAct 中间消息                │ ← 不可裁
└────────────────────────────────────────────────────────────┘
```

每段输出为一个或多个 OpenAI 兼容的 message 字典,最终拼成 `messages` 数组。

### 段位映射到 message role

| 段 | role | 备注 |
|---|---|---|
| 1 role | `system` | 单条 system message |
| 2 manifest | `system` | 与 role 合并（避免多 system） |
| 3 env | `system` | 同上 |
| 4 memory | `system` | 标记 `<memory>...</memory>` 块 |
| 5 kb | `system` | 标记 `<kb>...</kb>` 块,每片带 citation |
| 6 history | `user` / `assistant` / `tool` | 原样保留 |
| 7 current | `user` / `assistant` / `tool` | run 内累积的消息 |

---

## 3. 接口

```python
# src/agent/context_builder.py
from src.common.types import (
    PromptPayload, AgentContext, Message, RetrievalQuery, RetrievalHit
)

class ContextBuilder:
    def __init__(self, ctx: AgentContext):
        self.ctx = ctx
        self.services = ctx.services
        self.spec = ctx.spec
        # token 估算器（tiktoken 或 fallback）
        self.estimator = TokenEstimator(ctx.spec.preferred_model)

    async def build(self, run_messages: list[Message]) -> PromptPayload:
        """组装 prompt。run_messages 是当前 run 内累积的消息（含 user 起点 + assistant + tool）。"""
        # 1. 计算各段预算
        budgets = self._allocate_budget()

        # 2. 并发拉数据
        history_task = self._build_history(budgets["history"])
        memory_task = self._build_memory(budgets["memory"])
        kb_task = self._build_kb(budgets["kb"])
        history, memory, kb = await asyncio.gather(history_task, memory_task, kb_task)

        # 3. 固定段（无 IO）
        role = self._build_role()
        manifest = self._build_manifest()
        env = self._build_env()
        current = self._build_current(run_messages)

        # 4. 拼装 + 二次裁剪（防止超总额）
        messages, sections, dropped = self._assemble(
            role, manifest, env, memory, kb, history, current,
            total_budget=self._total_input_budget()
        )

        return PromptPayload(
            messages=messages,
            tools=self._render_tools(),
            model=self.spec.preferred_model,
            fallback_models=self.spec.fallback_models,
            temperature=self.spec.temperature,
            max_tokens=self.spec.max_tokens,
            token_estimate=sum(sections.values()),
            sections=sections,
            dropped=dropped,
        )
```

---

## 4. 预算分配算法

```python
def _allocate_budget(self) -> dict[str, int]:
    """根据 token_budget 总额 + max_tokens 输出预留,分配给各段。"""
    total = self.spec.token_budget                  # 例 50000
    output_reserved = self.spec.max_tokens          # 例 4096
    # 留 10% safety margin
    input_budget = int((total - output_reserved) * 0.9)

    # 固定段配额（绝对值）
    role = 1500
    manifest = 3000
    env = 500
    current = 8000        # 累积上限,超过用 history 压缩策略

    # 剩余分给可裁段
    remaining = input_budget - role - manifest - env - current
    # 召回类按比例
    memory = int(remaining * 0.20)
    kb = int(remaining * 0.30)
    history = remaining - memory - kb               # 占大头

    return {
        "role": role, "manifest": manifest, "env": env,
        "memory": memory, "kb": kb,
        "history": history, "current": current,
    }
```

### 4.1 裁剪优先级（从先裁到不可裁）

```
memory  >  loaded_skills(LRU)  >  kb  >  history(压缩)  >  hard_truncate
                                                              │
                                                  绝不动: role / env / current
```

具体策略：

| 段 | 超额时动作 |
|---|---|
| memory | 按 score 降序保留 top-K,截到 token 上限 |
| loaded_skills | 在 manifest 内 LRU 淘汰最久未用的 skill 描述 |
| kb | 同 memory,并优先丢弃同文档重复 chunk |
| history | 触发 SessionService.compress（生成 rolling summary 替换早期消息） |
| hard_truncate | 万不得已：从 history 头部按消息粒度删除（保留最近 N 条 + summary） |
| role / env / current | **永不裁剪**,超额直接抛 `BudgetExceededError` |

---

## 5. 各段实现

### 5.1 Role 段

```python
def _build_role(self) -> str:
    """system_prompt + 全局指令（变量替换）。"""
    template = self.spec.system_prompt or DEFAULT_SYSTEM_PROMPT
    return self._render_template(template, {
        "agent_name": self.spec.name,
        "user_name": self.ctx.user_id,            # 后续可加 user profile 注入
        "datetime": format_now(),
    })
```

`DEFAULT_SYSTEM_PROMPT`（兜底）：

```
你是 {agent_name},一个尽职、严谨、可解释的 AI 助理。
- 优先使用工具获取真实信息,不要编造
- 必要时调用 final_answer 工具结束本轮
- 回答用中文（除非用户用其他语言）
当前时间：{datetime}
```

### 5.2 Manifest 段

```python
def _build_manifest(self) -> str:
    """工具 + 已加载技能清单。"""
    tools = self.services.tool.list_for_agent(self.spec)
    skills = self._loaded_skills_summary()  # 见 §6

    lines = ["## 可用工具", *self._render_tool_briefs(tools)]
    if skills:
        lines += ["", "## 已加载技能", *skills]
    return "\n".join(lines)
```

> 注意：完整的 JSON Schema 是通过 `tools` 字段（function calling）给 LLM 的,manifest 段只是给一段**人类可读的 brief**,帮助模型理解工具用途,避免每个 schema 都重复描述。

### 5.3 Env 段

```python
def _build_env(self) -> str:
    return f"""## 运行环境
- 当前时间: {format_now()}
- 时区: {self.ctx.extra_inputs.get('tz', 'Asia/Shanghai')}
- 用户 ID: {self.ctx.user_id}
- Session ID: {self.ctx.session_id}
- Run ID: {self.ctx.run_id}
- 步数: {self.ctx.used_steps}/{self.spec.max_steps}
- 已用 tokens: {self.ctx.used_tokens}/{self.spec.token_budget}
"""
```

### 5.4 Memory 段

```python
async def _build_memory(self, budget: int) -> str:
    if budget <= 0:
        return ""
    query = self._derive_query()                  # 取最近一条 user message 文本
    if not query:
        return ""

    hits = await self.services.memory.recall(
        user_id=self.ctx.user_id,
        query=query,
        top_k=10,
        diversify=True,
    )
    rendered = self._render_memory_hits(hits, budget)
    return f"<memory>\n{rendered}\n</memory>" if rendered else ""

def _render_memory_hits(self, hits, budget):
    out, used = [], 0
    for h in hits:
        line = f"- [{h.metadata.get('kind', 'note')}] {h.text}"
        cost = self.estimator.estimate(line)
        if used + cost > budget:
            break
        out.append(line)
        used += cost
    return "\n".join(out)
```

### 5.5 KB 段

```python
async def _build_kb(self, budget: int) -> str:
    if budget <= 0 or not self._kb_enabled():
        return ""
    query = self._derive_query()
    hits = await self.services.retriever.search(
        RetrievalQuery(
            text=query, user_id=self.ctx.user_id,
            channels=["kb"], top_k=8, diversify=True,
        )
    )
    return self._render_kb_hits(hits, budget)

def _render_kb_hits(self, hits, budget):
    blocks, used = [], 0
    for h in hits:
        cite = h.citation
        block = (
            f'<chunk doc="{cite.get("title", "")}" '
            f'path="{">".join(cite.get("heading_path", []))}" '
            f'chunk_id="{h.chunk_id}">\n'
            f"{h.text}\n"
            f"</chunk>"
        )
        cost = self.estimator.estimate(block)
        if used + cost > budget:
            break
        blocks.append(block)
        used += cost
    return f"<kb>\n{''.join(blocks)}\n</kb>" if blocks else ""
```

### 5.6 History 段

```python
async def _build_history(self, budget: int) -> list[dict]:
    """近 N 轮 + rolling summary。"""
    window = await self.services.session.get_window_for_context(
        session_id=self.ctx.session_id,
        max_tokens=budget,
        # 把 tool_call/tool 配对作为整体保留,不允许半截
        keep_tool_pairs=True,
    )
    return [self._msg_to_dict(m) for m in window.messages]
```

> Window 内部包含：rolling summary（如有）作为单独 system message + 近 N 条原始消息。

### 5.7 Current 段

```python
def _build_current(self, run_messages: list[Message]) -> list[dict]:
    """run 内累积的 user/assistant/tool 消息（最早是触发本 run 的 user 消息）。"""
    return [self._msg_to_dict(m) for m in run_messages]
```

---

## 6. 技能（Skill）渲染策略

技能有两种使用模式：

### 6.1 In-context 模式

技能是一段说明文档（procedural）,通过 `load_skill` 工具加载到 `loaded_skills` 列表。
渲染规则：

```python
def _loaded_skills_summary(self) -> list[str]:
    skills = self.ctx.extra_inputs.get("loaded_skills", [])  # OrderedDict (LRU)
    out = []
    for skill in skills:
        out.append(f"### Skill: {skill.name}\n{skill.content}")
    return out
```

LRU 上限：3 个技能同时加载;超过则淘汰最久未访问。

### 6.2 Delegated 模式

技能本身是一个 SubAgent（或带工具的复杂流程）。manifest 中只列名,由 `delegate_task` 工具触发执行。

```python
def _render_tool_briefs(self, tools):
    out = []
    for t in tools:
        out.append(f"- **{t.name}**: {t.description}")
    return out
```

---

## 7. Tool Schema 输出

```python
def _render_tools(self) -> list[dict]:
    """转换为 OpenAI function calling JSON Schema。"""
    tools = self.services.tool.list_for_agent(self.spec)
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.schema,        # 已是 JSON Schema dict
            },
        }
        for t in tools
    ]
```

LLMGateway 内部再根据 provider 转 Anthropic 的 `tools[].input_schema` 形态。

---

## 8. Token 估算

```python
class TokenEstimator:
    def __init__(self, model: str):
        self.encoder = self._load_encoder(model)

    def _load_encoder(self, model: str):
        try:
            import tiktoken
            return tiktoken.encoding_for_model(model)
        except Exception:
            return None  # fallback: 按字符 / 3.5

    def estimate(self, text: str) -> int:
        if self.encoder:
            return len(self.encoder.encode(text))
        # 中英混合估算：CJK 字符 1.5 token/字,ASCII 0.3 token/字
        cjk = sum(1 for c in text if ord(c) > 0x4E00)
        ascii_n = len(text) - cjk
        return int(cjk * 1.5 + ascii_n * 0.3)

    def estimate_messages(self, messages: list[dict]) -> int:
        # OpenAI 每条额外 ~4 token (role + 分隔)
        return sum(self.estimate(m.get("content", "")) + 4 for m in messages)
```

---

## 9. 二次裁剪与最终拼装

```python
def _assemble(self, role, manifest, env, memory, kb, history, current, total_budget):
    sections = {}
    dropped = []

    system_parts = [role, manifest, env]
    if memory:
        system_parts.append(memory)
    if kb:
        system_parts.append(kb)
    system_text = "\n\n".join(system_parts)

    sections["role"] = self.estimator.estimate(role)
    sections["manifest"] = self.estimator.estimate(manifest)
    sections["env"] = self.estimator.estimate(env)
    sections["memory"] = self.estimator.estimate(memory) if memory else 0
    sections["kb"] = self.estimator.estimate(kb) if kb else 0
    sections["history"] = self.estimator.estimate_messages(history)
    sections["current"] = self.estimator.estimate_messages(current)

    # 二次校验
    actual = sum(sections.values())
    if actual > total_budget:
        # 按优先级再裁
        if sections["memory"] > 0:
            memory = ""
            dropped.append("memory")
            sections["memory"] = 0
            actual = sum(sections.values())
        if actual > total_budget and sections["kb"] > 0:
            kb = ""
            dropped.append("kb")
            sections["kb"] = 0
            actual = sum(sections.values())
        if actual > total_budget:
            # 触发 hard truncate history
            history, dropped_h = self._hard_truncate_history(history, total_budget - actual + sections["history"])
            sections["history"] = self.estimator.estimate_messages(history)
            dropped.extend(dropped_h)

    # 重新组合 system
    system_parts = [role, manifest, env]
    if memory: system_parts.append(memory)
    if kb: system_parts.append(kb)

    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    messages.extend(history)
    messages.extend(current)
    return messages, sections, dropped
```

---

## 10. 数据流图

```
                   AgentContext (run_messages)
                         │
                         ↓
                  ContextBuilder.build
              ┌──────────┼──────────────┐
              ↓          ↓              ↓
        SessionService MemoryService  Retriever
              │          │              │
            history    memory hits    kb hits
              └──────────┼──────────────┘
                         ↓
                    _assemble + _hard_truncate
                         ↓
                   PromptPayload
                  (messages + tools + sections)
                         ↓
                    LLMGateway.stream
```

---

## 11. 单元测试

```
tests/unit/agent/context_builder/
├── test_budget_allocation.py    # 各种 token_budget 下分配是否合理
├── test_role_render.py          # 模板变量替换
├── test_manifest_filter.py      # allowed_tools 过滤
├── test_memory_truncate.py      # 超 budget 时截断
├── test_kb_render.py            # citation 元信息正确
├── test_history_window.py       # mock SessionService 返回,验证拼装
├── test_assemble_drop.py        # 总 budget 不够时按优先级 drop
├── test_hard_truncate.py        # history 头部裁剪不破坏 tool pair
└── test_token_estimator.py      # tiktoken 与 fallback 一致性
```

### 关键测试用例

```python
@pytest.mark.asyncio
async def test_budget_overflow_drops_memory_first(mock_ctx):
    builder = ContextBuilder(mock_ctx)
    # 故意把 memory 撑满
    mock_ctx.services.memory.recall.return_value = make_memory_hits(count=100, each_tokens=500)
    payload = await builder.build(run_messages=[user_msg("hi")])
    assert "memory" in payload.dropped or payload.sections["memory"] < 50000
    assert payload.token_estimate <= mock_ctx.spec.token_budget

@pytest.mark.asyncio
async def test_history_keeps_tool_pair_atomic(mock_ctx):
    """裁剪 history 时不能只删 tool_call 不删对应 tool 消息。"""
    ...
```

---

## 12. 性能注意

- 三段并发 IO：`asyncio.gather(history, memory, kb)`,避免串行
- TokenEstimator 缓存 encoder 实例（每个进程一次加载）
- 同一 run 内 builder 不复用：每轮重新 build（因为 run_messages 变了）
- 但 `_render_tools()` 输出可以缓存到 AgentContext.extra_inputs（spec 不变就不变）

---

## 13. 与其他模块的契约

| 调用方 | 调用点 | 入参 | 出参 |
|---|---|---|---|
| BaseAgent.run_loop | 每轮迭代开始 | `run_messages: list[Message]` | `PromptPayload` |
| LLMGateway.stream | 接收 builder 输出 | `PromptPayload` | `AsyncIterator[LLMChunk]` |
| SessionService | 被 ContextBuilder 调用 | `session_id, max_tokens` | `Window(messages, summary)` |
| MemoryService | 被 ContextBuilder 调用 | `user_id, query, top_k` | `list[RetrievalHit]` |
| Retriever | 被 ContextBuilder 调用 | `RetrievalQuery` | `list[RetrievalHit]` |
| ToolRegistry | 被 ContextBuilder 调用 | `AgentSpec` | `list[Tool]` |

---

## 14. 设计 FAQ

**Q: 为什么不让 LLM 自己 RAG?**
A: 个人助理场景对延迟敏感,每轮决策"要不要召回"会增加一次模型往返。固定召回更稳定。

**Q: 为什么 memory 优先级低于 kb?**
A: memory 有可能是脏数据（错误抽取）,且模型本身常识可补;kb 是用户上传的精确参考,丢失会直接影响答案质量。

**Q: 为什么 current 不可裁?**
A: current 是当前 run 内的 user 消息 + ReAct 中间结果,裁了会让 LLM 失忆,整个循环崩溃。