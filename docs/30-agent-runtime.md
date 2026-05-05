# 30 · Agent 编排层 —— Runtime / Orchestrator / BaseAgent / Registry

> **职责**：实现 Agent 的执行核心。Runtime 单例承载全局服务,Orchestrator 编排单次 Run,BaseAgent 跑 ReAct 循环。
> **位置**：`src/agent/`

---

## 1. 模块清单

| 模块 | 文件 | 角色 |
|---|---|---|
| Runtime | `runtime.py` | 进程级单例 / DI 容器 / 生命周期管理 |
| AgentRegistry | `registry.py` | AgentSpec 加载、缓存、热更新 |
| Orchestrator | `orchestrator.py` | 单次 Run 编排,spawn SubAgent |
| BaseAgent | `base_agent.py` | ReAct loop 核心循环 |
| ContextBuilder | `context_builder.py` | → 见 [31-context-builder.md](./31-context-builder.md) |
| ToolExecutor | `tool_executor.py` | → 见 [32-tool-executor.md](./32-tool-executor.md) |
| LLMGateway | `llm_gateway.py` | → 见 [33-llm-gateway.md](./33-llm-gateway.md) |
| EventBus / RunRegistry | `run_registry.py` | → 见 [34-eventbus-runregistry.md](./34-eventbus-runregistry.md) |
| Builtin Tools | `builtin_tools/` | load_skill / delegate_task / final_answer / ... |

---

## 2. Runtime（单例容器）

### 2.1 职责

- 进程启动时一次性构造所有共享服务
- FastAPI lifespan 钩子集成
- 提供 `start_run()` 入口给 Gateway 调用
- 优雅关闭：等待所有 in-flight runs 结束 / 超时强制取消

### 2.2 接口

```python
# src/agent/runtime.py
class Runtime:
    """进程级单例。整个应用只有一个实例。"""

    _instance: "Runtime | None" = None

    def __init__(self, *, infra: Infra, services: ServiceContainer,
                 llm: "LLMGateway", agent_registry: "AgentRegistry",
                 tool_registry: "ToolRegistry",
                 run_registry: "RunRegistry",
                 event_bus: "EventBus", config: AppConfig):
        self._infra = infra
        self._services = services
        self._llm = llm
        self._agents = agent_registry
        self._tools = tool_registry
        self._runs = run_registry
        self._bus = event_bus
        self._config = config

        # 后台 worker 句柄
        self._workers: list[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()

    @classmethod
    async def initialize(cls, config_path: str) -> "Runtime":
        """从配置文件构造。Gateway 启动时调用一次。"""
        cfg = load_config(config_path)
        infra = await build_infra(cfg.infra)
        # LLMGateway 先构造（services 依赖它）
        llm = await build_llm_gateway(cfg.llm, infra)
        services = await build_services(infra, llm, cfg)
        agent_registry = AgentRegistry(path=cfg.agent.registry_path)
        await agent_registry.load()
        tool_registry = ToolRegistry()
        await tool_registry.load_builtins()
        await tool_registry.load_from_dir("src/tools")
        run_registry = RunRegistry(store=infra.relational)
        await run_registry.init()

        rt = cls(
            infra=infra, services=services, llm=llm,
            agent_registry=agent_registry, tool_registry=tool_registry,
            run_registry=run_registry, event_bus=infra.eventbus, config=cfg,
        )
        cls._instance = rt
        await rt._start_workers()
        return rt

    @classmethod
    def current(cls) -> "Runtime":
        if cls._instance is None:
            raise RuntimeError("Runtime not initialized")
        return cls._instance

    async def shutdown(self, timeout: float = 30.0) -> None:
        """优雅关闭：通知所有 worker,等待 in-flight runs。"""
        log.info("runtime_shutdown_begin")
        self._shutdown_event.set()
        # 取消所有 active runs（强制）
        active_runs = await self._runs.list_active()
        for r in active_runs:
            await self._runs.cancel(r.run_id, "shutdown")
        # 等待 worker 完成
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._workers, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log.warning("shutdown_timeout, forcing")
            for w in self._workers:
                w.cancel()
        await self._infra.relational.close()
        log.info("runtime_shutdown_done")

    async def _start_workers(self) -> None:
        """启动后台 worker（KB ingest, Memory compress, Artifact GC）。"""
        from src.service.kb_worker import KBIngestWorker
        from src.service.memory_worker import MemoryWorker
        from src.service.artifact_gc import ArtifactGCWorker

        kb_worker = KBIngestWorker(self._services.kb, self._infra.jobs, "kb-1")
        mem_worker = MemoryWorker(self._services.memory, self._infra.jobs, "mem-1")
        gc_worker = ArtifactGCWorker(self._services.artifact, interval_sec=3600)

        self._workers = [
            asyncio.create_task(kb_worker.run(self._shutdown_event)),
            asyncio.create_task(mem_worker.run(self._shutdown_event)),
            asyncio.create_task(gc_worker.run(self._shutdown_event)),
        ]

    # === 主入口 ===
    async def start_run(self, *, agent_name: str, user_id: str,
                        session_id: str, user_message: str,
                        extra_inputs: dict | None = None,
                        trace_id: str = "") -> str:
        """Gateway 调用：启动一次 run,立即返回 run_id（不等执行完成）。"""
        spec = await self._agents.resolve(agent_name)
        if not spec:
            raise NotFoundError(f"agent not found: {agent_name}")

        # 校验权限：agent 是否对该用户开放
        # ...

        run_id = RUN_ID()
        ctx = AgentContext(
            run_id=run_id,
            parent_run_id=None,
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id or run_id,
            spec=spec,
            user_message=Message(role="user", content=user_message,
                                 created_at=now_ts()),
            extra_inputs=extra_inputs or {},
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + spec.deadline_sec,
            started_at=now_ts(),
            services=self._services_handle(),
        )
        await self._runs.register(run_id, ctx, agent_name=spec.name,
                                  parent_run_id=None)

        orch = Orchestrator(ctx=ctx, runtime=self)
        # fire-and-forget,run 执行结果通过 event 流汇报
        asyncio.create_task(self._run_with_lifecycle(orch, ctx))
        return run_id

    async def _run_with_lifecycle(self, orch: "Orchestrator", ctx: AgentContext):
        try:
            await orch.run()
        except CancelledError:
            await self._runs.update_state(ctx.run_id, "cancelled")
        except TimeoutError:
            await self._runs.update_state(ctx.run_id, "timeout")
        except Exception as e:
            log.exception("run_failed", run_id=ctx.run_id)
            await self._runs.update_state(ctx.run_id, "failed", error=str(e))
            await self._bus.publish(Event(
                type=EventType.RUN_FAILED,
                run_id=ctx.run_id, session_id=ctx.session_id,
                user_id=ctx.user_id, ts=now_ts(),
                payload={"error": str(e)},
                level="error",
            ))

    def _services_handle(self) -> Services:
        """构造 Services 句柄注入到 AgentContext。"""
        return Services(
            session=self._services.session,
            memory=self._services.memory,
            artifact=self._services.artifact,
            kb=self._services.kb,
            retriever=self._services.retriever,
            embedding=self._services.embedding,
            skill=self._services.skill,
            tool=self._tools,
            llm=self._llm,
            event_bus=self._bus,
            run_registry=self._runs,
            agent_registry=self._agents,
        )
```

### 2.3 FastAPI 集成

```python
# src/gateway/app.py（节选）
@asynccontextmanager
async def lifespan(app: FastAPI):
    rt = await Runtime.initialize(os.environ["CONFIG_PATH"])
    app.state.runtime = rt
    yield
    await rt.shutdown(timeout=30.0)

app = FastAPI(lifespan=lifespan)
```

---

## 3. AgentRegistry

### 3.1 职责

- 加载 `src/agents/*.yaml` 中的 AgentSpec
- 维持内存索引 + mtime 热更新
- 解析（resolve）：根据名称查找;不存在抛 NotFoundError

### 3.2 AgentSpec YAML 格式

`src/agents/research-agent.yaml`：

```yaml
name: research-agent
description: 通用研究助手,擅长多步搜索 + 综合
cls: src.agent.base_agent.BaseAgent  # 默认值,可省略
system_prompt: |
  你是一个研究助手。任务是帮用户深度调研问题,输出有引用的结构化报告。
  ...

preferred_model: claude-sonnet-4
fallback_models:
  - gpt-4o
  - claude-haiku-4

temperature: 0.3
max_tokens: 4096

allowed_tools:
  - web_search
  - web_browser
  - read_artifact
  - generate_doc
  - final_answer

allowed_skills:
  - lark-docs

max_steps: 30
token_budget: 100000
deadline_sec: 600
max_parallel_tools: 3
```

### 3.3 接口

```python
# src/agent/registry.py
class AgentRegistry:
    def __init__(self, path: str):
        self._path = Path(path)
        self._cache: dict[str, AgentSpec] = {}
        self._mtime: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        """启动时全量加载。"""
        async with self._lock:
            self._cache.clear()
            self._mtime.clear()
            for f in self._path.glob("*.yaml"):
                spec = self._load_file(f)
                self._cache[spec.name] = spec
                self._mtime[spec.name] = f.stat().st_mtime

    async def resolve(self, name: str) -> AgentSpec | None:
        """查找 + mtime 热更新。"""
        async with self._lock:
            spec = self._cache.get(name)
            if spec and spec.source_path:
                f = Path(spec.source_path)
                if f.exists() and f.stat().st_mtime > self._mtime[name]:
                    spec = self._load_file(f)
                    self._cache[name] = spec
                    self._mtime[name] = f.stat().st_mtime
            return spec

    async def list(self) -> list[AgentSpec]:
        async with self._lock:
            return list(self._cache.values())

    async def register(self, spec: AgentSpec) -> None:
        """代码侧动态注册（用于内置 agent / 测试）。"""
        async with self._lock:
            spec.source = "code"
            self._cache[spec.name] = spec

    def _load_file(self, f: Path) -> AgentSpec:
        with open(f) as fp:
            data = yaml.safe_load(fp)
        cls_path = data.pop("cls", None)
        cls = BaseAgent if not cls_path else _import_class(cls_path)
        return AgentSpec(
            cls=cls,
            source="registry",
            source_path=str(f.absolute()),
            mtime=f.stat().st_mtime,
            **{k: v for k, v in data.items()
               if k in AgentSpec.__dataclass_fields__},
        )
```

### 3.4 Ephemeral Spec（临时 Agent）

允许代码运行时临时构造 spec（不写文件）,主要用于 SubAgent 的特殊场景：

```python
spec = AgentSpec(
    name="adhoc-summarizer",
    description="内联生成的临时 agent",
    cls=BaseAgent,
    system_prompt="...",
    preferred_model="claude-haiku-4",
    allowed_tools={"final_answer"},
    max_steps=3,
    token_budget=5000,
    source="ephemeral",
)
```

### 3.5 测试用例

```
tests/unit/agent/test_registry.py
- test_load_all_yaml_files
- test_resolve_by_name
- test_resolve_missing_returns_none
- test_hot_reload_on_mtime_change
- test_register_code_spec
```

---

## 4. Orchestrator（per-Run 编排）

### 4.1 职责

- 单次 Run 的"船长",封装 BaseAgent 实例的生命周期
- 处理 SubAgent spawn（delegate_task 工具的实际执行点）
- run.started / run.completed 事件发布
- 异常兜底（不让 agent 异常向上裸奔）

### 4.2 接口

```python
# src/agent/orchestrator.py
class Orchestrator:
    def __init__(self, *, ctx: AgentContext, runtime: Runtime):
        self._ctx = ctx
        self._runtime = runtime
        self._agent: BaseAgent | None = None

    async def run(self) -> Message:
        """执行完整 ReAct loop,返回 final assistant message。"""
        ctx = self._ctx
        bus = ctx.services.event_bus

        # 实例化 agent
        agent_cls = ctx.spec.cls or BaseAgent
        self._agent = agent_cls(ctx=ctx, orchestrator=self)

        # 状态变更：running
        await ctx.services.run_registry.update_state(ctx.run_id, "running")
        await bus.publish(Event(
            type=EventType.RUN_STARTED,
            run_id=ctx.run_id, parent_run_id=ctx.parent_run_id,
            session_id=ctx.session_id, user_id=ctx.user_id,
            ts=now_ts(),
            payload={"agent_name": ctx.spec.name},
        ))

        try:
            # 跑 ReAct
            final = await self._agent.run_loop()
        except CancelledError:
            await bus.publish(Event(
                type=EventType.RUN_CANCELLED,
                run_id=ctx.run_id, session_id=ctx.session_id,
                user_id=ctx.user_id, ts=now_ts(), payload={},
            ))
            raise

        # 持久化对话
        await self._persist_run_messages()

        # 发完成事件
        await ctx.services.run_registry.update_state(
            ctx.run_id, "completed",
            used_tokens=ctx.used_tokens, used_steps=ctx.used_steps,
        )
        await bus.publish(Event(
            type=EventType.RUN_COMPLETED,
            run_id=ctx.run_id, session_id=ctx.session_id,
            user_id=ctx.user_id, ts=now_ts(),
            payload={
                "used_tokens": ctx.used_tokens,
                "used_steps": ctx.used_steps,
                "final_message": final.content[:500],
            },
        ))
        return final

    async def spawn_subagent(self, *, agent_name: str, task: str,
                             token_budget: int | None = None,
                             deadline_sec: int | None = None,
                             extra_inputs: dict | None = None) -> Message:
        """delegate_task 工具调用此方法。同步等待 SubAgent 完成。"""
        parent_ctx = self._ctx
        spec = await parent_ctx.services.agent_registry.resolve(agent_name)
        if not spec:
            raise NotFoundError(f"subagent not found: {agent_name}")

        # 构造 sub context（独立 budget / cancel_event）
        sub_run_id = RUN_ID()
        sub_ctx = AgentContext(
            run_id=sub_run_id,
            parent_run_id=parent_ctx.run_id,
            session_id=parent_ctx.session_id,
            user_id=parent_ctx.user_id,
            trace_id=parent_ctx.trace_id,
            spec=replace(spec,
                token_budget=token_budget or spec.token_budget,
                deadline_sec=deadline_sec or spec.deadline_sec,
            ),
            user_message=Message(role="user", content=task,
                                 created_at=now_ts()),
            extra_inputs=extra_inputs or {},
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + (deadline_sec or spec.deadline_sec),
            started_at=now_ts(),
            services=parent_ctx.services,
        )

        await parent_ctx.services.run_registry.register(
            sub_run_id, sub_ctx, agent_name=spec.name,
            parent_run_id=parent_ctx.run_id,
        )
        await parent_ctx.services.event_bus.publish(Event(
            type=EventType.SUBAGENT_STARTED,
            run_id=parent_ctx.run_id, parent_run_id=parent_ctx.parent_run_id,
            session_id=parent_ctx.session_id, user_id=parent_ctx.user_id,
            ts=now_ts(),
            payload={"sub_run_id": sub_run_id, "agent_name": spec.name,
                     "task": task},
        ))

        sub_orch = Orchestrator(ctx=sub_ctx, runtime=self._runtime)
        try:
            final = await sub_orch.run()
            await parent_ctx.services.event_bus.publish(Event(
                type=EventType.SUBAGENT_COMPLETED,
                run_id=parent_ctx.run_id, session_id=parent_ctx.session_id,
                user_id=parent_ctx.user_id, ts=now_ts(),
                payload={"sub_run_id": sub_run_id, "result": final.content[:500]},
            ))
            return final
        except Exception as e:
            log.exception("subagent_failed", sub_run_id=sub_run_id)
            return Message(role="assistant",
                content=f"[SubAgent failed: {e}]")

    async def _persist_run_messages(self) -> None:
        """run 完成后写入 SessionService。"""
        ctx = self._ctx
        if self._agent and self._agent.run_messages:
            await ctx.services.session.append_messages(
                session_id=ctx.session_id, user_id=ctx.user_id,
                msgs=self._agent.run_messages, run_id=ctx.run_id,
            )
            # 触发 summary 检查
            await ctx.services.session.maybe_summarize(
                ctx.session_id, ctx.user_id,
            )
```

### 4.3 测试用例

```
tests/unit/agent/test_orchestrator.py
- test_run_publishes_started_and_completed
- test_run_persists_messages_to_session
- test_run_handles_agent_exception
- test_spawn_subagent_creates_independent_context
- test_subagent_isolated_budget
- test_subagent_inherits_session_user
```

---

## 5. BaseAgent（ReAct loop 核心）

### 5.1 职责

- 实现 ReAct 循环：perceive → plan → act → observe → reflect
- 管理 run 内的消息累积（self.run_messages）
- 处理 LLM 流式 chunk → 转 tool_calls → 执行 → 注入 observation
- 终止条件检查（final_answer / max_steps / budget / deadline / cancel）

### 5.2 接口

```python
# src/agent/base_agent.py
class BaseAgent:
    def __init__(self, *, ctx: AgentContext, orchestrator: Orchestrator):
        self.ctx = ctx
        self.orch = orchestrator
        self.run_messages: list[Message] = []   # 本次 run 累积的消息
        self._final_message: Message | None = None

    async def run_loop(self) -> Message:
        """主循环。返回 final assistant message。"""
        ctx = self.ctx
        # 把 user message 加入 run_messages
        self.run_messages.append(ctx.user_message)

        builder = ContextBuilder(ctx)

        while True:
            # === 终止检查 ===
            self._check_termination()

            # === PERCEIVE ===
            payload = await builder.build(self.run_messages)
            ctx.used_tokens += 0  # 占位,下面 LLM 返回 usage 时再加

            # === PLAN（流式 LLM）===
            assistant_msg, tool_calls = await self._stream_llm(payload)
            self.run_messages.append(assistant_msg)
            ctx.used_steps += 1

            # 检查是否调用了 final_answer
            final_call = next((tc for tc in tool_calls
                              if tc.name == "final_answer"), None)
            if final_call:
                # final_answer 不需要执行（其本身是终止信号）,直接返回
                self._final_message = Message(
                    role="assistant",
                    content=final_call.arguments.get("answer", ""),
                    metadata={"final_answer_args": final_call.arguments},
                    created_at=now_ts(),
                )
                self.run_messages.append(self._final_message)
                return self._final_message

            # 没有 tool_calls → 视为最终回答
            if not tool_calls:
                self._final_message = assistant_msg
                return assistant_msg

            # === ACT ===
            executor = ToolExecutor(ctx, self.orch)
            results = await executor.execute(tool_calls)

            # === OBSERVE ===
            for r in results:
                tool_msg = Message(
                    role="tool",
                    tool_call_id=r.call_id,
                    name=r.name,
                    content=r.content,
                    metadata={"status": r.status, "artifact_id": r.artifact_id},
                    created_at=now_ts(),
                )
                self.run_messages.append(tool_msg)

            # === 进入下一轮 ===

    def _check_termination(self):
        ctx = self.ctx
        if ctx.cancel_event.is_set():
            raise CancelledError()
        if now_ts() > ctx.deadline_at:
            raise TimeoutError(f"run {ctx.run_id} deadline reached")
        if ctx.used_tokens > ctx.spec.token_budget:
            raise BudgetExceededError(f"token budget exceeded")
        if ctx.used_steps >= ctx.spec.max_steps:
            raise BudgetExceededError(f"max steps reached")

    async def _stream_llm(self, payload: PromptPayload) -> tuple[Message, list[ToolCall]]:
        """流式调用 LLM,发布事件,组装最终 assistant message。"""
        ctx = self.ctx
        bus = ctx.services.event_bus

        text_parts: list[str] = []
        tool_calls_buf: dict[int, ToolCall] = {}   # index -> ToolCall

        try:
            async for chunk in ctx.services.llm.stream(payload, cancel_event=ctx.cancel_event):
                if chunk.type == "text_delta":
                    text_parts.append(chunk.text)
                    await bus.publish(Event(
                        type=EventType.LLM_TEXT_DELTA,
                        run_id=ctx.run_id, parent_run_id=ctx.parent_run_id,
                        session_id=ctx.session_id, user_id=ctx.user_id,
                        ts=now_ts(),
                        payload={"text": chunk.text},
                    ))
                elif chunk.type == "tool_call_start":
                    tool_calls_buf[chunk.tool_call_index] = ToolCall(
                        call_id=chunk.tool_call_id,
                        name=chunk.tool_name,
                        arguments={},
                        arguments_raw="",
                    )
                    await bus.publish(Event(
                        type=EventType.LLM_TOOL_CALL_START,
                        run_id=ctx.run_id, session_id=ctx.session_id,
                        user_id=ctx.user_id, ts=now_ts(),
                        payload={"call_id": chunk.tool_call_id,
                                 "name": chunk.tool_name},
                    ))
                elif chunk.type == "tool_call_delta":
                    tc = tool_calls_buf[chunk.tool_call_index]
                    tc.arguments_raw += chunk.arguments_delta
                elif chunk.type == "tool_call_end":
                    tc = tool_calls_buf[chunk.tool_call_index]
                    tc.arguments = chunk.arguments_full or _parse_json(tc.arguments_raw)
                    await bus.publish(Event(
                        type=EventType.LLM_TOOL_CALL_END,
                        run_id=ctx.run_id, session_id=ctx.session_id,
                        user_id=ctx.user_id, ts=now_ts(),
                        payload={"call_id": tc.call_id, "name": tc.name,
                                 "arguments": tc.arguments},
                    ))
                elif chunk.type == "usage":
                    ctx.used_tokens += chunk.usage.total_tokens
                    await bus.publish(Event(
                        type=EventType.LLM_USAGE,
                        run_id=ctx.run_id, session_id=ctx.session_id,
                        user_id=ctx.user_id, ts=now_ts(),
                        payload=asdict(chunk.usage),
                    ))
                elif chunk.type == "stop":
                    break
                elif chunk.type == "error":
                    await bus.publish(Event(
                        type=EventType.LLM_ERROR,
                        run_id=ctx.run_id, session_id=ctx.session_id,
                        user_id=ctx.user_id, ts=now_ts(),
                        payload=asdict(chunk.error),
                        level="error",
                    ))
                    raise LLMProviderError(chunk.error.message, chunk.error)
        except CancelledError:
            raise

        full_text = "".join(text_parts)
        tcs = sorted(tool_calls_buf.values(), key=lambda x: x.call_id)
        msg = Message(
            role="assistant",
            content=full_text,
            tool_calls=tcs,
            created_at=now_ts(),
        )
        return msg, tcs
```

### 5.3 自定义 Agent 派生

90% 场景用 BaseAgent 即可。如果某个 agent 需要定制行为,继承重写：

```python
class StreamingResearchAgent(BaseAgent):
    """重写 PERCEIVE 阶段,每轮强制把最近的 KB 检索结果带上。"""
    async def run_loop(self):
        # 调父类,但插入 hook
        ...
```

### 5.4 测试用例

```
tests/unit/agent/test_base_agent.py
- test_loop_terminates_on_final_answer
- test_loop_terminates_on_no_tool_calls
- test_loop_respects_max_steps
- test_loop_respects_token_budget
- test_loop_respects_cancel_event
- test_loop_respects_deadline
- test_stream_assembles_text_and_tool_calls
- test_stream_publishes_events
- test_observation_appends_tool_messages
- test_subagent_call_via_delegate_task
```

---

## 6. ToolRegistry（工具注册表）

### 6.1 职责

- 加载内建工具 + 用户自定义工具
- 提供 lookup 接口
- 工具元数据（schema / description）供 ContextBuilder 渲染

### 6.2 Tool Protocol

```python
# src/agent/tool_protocol.py
class Tool(Protocol):
    name: str
    description: str
    schema: dict           # JSON Schema for arguments
    scopes: list[str]      # 权限标签
    is_builtin: bool
    cacheable: bool        # 是否支持结果缓存

    async def execute(self, *, arguments: dict, ctx: AgentContext,
                      orchestrator: "Orchestrator") -> ToolResult: ...
```

### 6.3 接口

```python
# src/agent/tool_registry.py
class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    async def load_builtins(self) -> None:
        from src.agent.builtin_tools import (
            FinalAnswerTool, LoadSkillTool, DelegateTaskTool,
            ReadArtifactTool, RememberTool, ForgetTool,
        )
        for cls in [FinalAnswerTool, LoadSkillTool, DelegateTaskTool,
                    ReadArtifactTool, RememberTool, ForgetTool]:
            t = cls()
            self._tools[t.name] = t

    async def load_from_dir(self, path: str) -> None:
        """扫描 src/tools/ 目录,每个 .py 文件导出的 TOOL 变量。"""
        for f in Path(path).rglob("*.py"):
            mod = importlib.import_module(_to_module_path(f))
            tool = getattr(mod, "TOOL", None)
            if tool:
                self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_for_agent(self, allowed: set[str]) -> list[Tool]:
        return [t for n, t in self._tools.items() if n in allowed]
```

---

## 7. 内建工具

### 7.1 final_answer

```python
# src/agent/builtin_tools/final_answer.py
class FinalAnswerTool:
    name = "final_answer"
    description = "提供最终回答给用户。调用此工具会结束当前 Agent 执行。"
    schema = {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "给用户看的最终答复"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["answer"],
    }
    scopes = []
    is_builtin = True
    cacheable = False

    async def execute(self, *, arguments, ctx, orchestrator):
        # 实际 BaseAgent 的 run_loop 在解析到这个 tool_call 时直接终止,
        # 不会进入 ToolExecutor。这里写个空实现作兜底。
        return ToolResult(
            call_id="", name=self.name, status="ok",
            content=arguments.get("answer", ""),
        )
```

### 7.2 delegate_task

```python
class DelegateTaskTool:
    name = "delegate_task"
    description = "派发子任务给指定的 SubAgent。同步返回子 Agent 的最终答复。"
    schema = {
        "type": "object",
        "properties": {
            "agent_name": {"type": "string"},
            "task": {"type": "string"},
            "token_budget": {"type": "integer", "default": 20000},
            "deadline_sec": {"type": "integer", "default": 120},
        },
        "required": ["agent_name", "task"],
    }
    scopes = []
    is_builtin = True
    cacheable = False

    async def execute(self, *, arguments, ctx, orchestrator):
        try:
            result_msg = await orchestrator.spawn_subagent(
                agent_name=arguments["agent_name"],
                task=arguments["task"],
                token_budget=arguments.get("token_budget"),
                deadline_sec=arguments.get("deadline_sec"),
            )
            return ToolResult(
                call_id="", name=self.name, status="ok",
                content=result_msg.content,
                metadata={"sub_run_id": result_msg.metadata.get("run_id")},
            )
        except Exception as e:
            return ToolResult(
                call_id="", name=self.name, status="error",
                error_type=type(e).__name__, error_message=str(e),
            )
```

### 7.3 load_skill

```python
class LoadSkillTool:
    name = "load_skill"
    description = "加载指定技能。下一轮 prompt 会包含该技能的详细说明。"
    schema = {
        "type": "object",
        "properties": {"skill_name": {"type": "string"}},
        "required": ["skill_name"],
    }
    scopes = []
    is_builtin = True
    cacheable = False

    async def execute(self, *, arguments, ctx, orchestrator):
        skill = await ctx.services.skill.get(arguments["skill_name"])
        if not skill:
            return ToolResult(call_id="", name=self.name, status="error",
                error_message=f"skill not found: {arguments['skill_name']}")
        # 标记为已加载,ContextBuilder 下一轮会加上
        ctx.extra_inputs.setdefault("loaded_skills", []).append(skill.name)
        return ToolResult(
            call_id="", name=self.name, status="ok",
            content=f"skill '{skill.name}' loaded. 下一轮可参考其说明。",
        )
```

### 7.4 read_artifact

```python
class ReadArtifactTool:
    name = "read_artifact"
    description = "读取一个 artifact 的完整内容。"
    schema = {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "max_chars": {"type": "integer", "default": 20000,
                          "description": "返回内容最大字符数,超出截断"},
        },
        "required": ["artifact_id"],
    }
    scopes = []
    is_builtin = True
    cacheable = True   # 同 artifact_id 结果不变

    async def execute(self, *, arguments, ctx, orchestrator):
        try:
            text = await ctx.services.artifact.get_text(
                arguments["artifact_id"], ctx.user_id,
            )
            limit = arguments.get("max_chars", 20000)
            truncated = len(text) > limit
            return ToolResult(
                call_id="", name=self.name, status="ok",
                content=text[:limit] + ("\n...[truncated]" if truncated else ""),
            )
        except NotFoundError as e:
            return ToolResult(call_id="", name=self.name, status="error",
                error_type="NotFound", error_message=str(e))
```

### 7.5 remember / forget

```python
class RememberTool:
    name = "remember"
    description = "把一条信息显式写入用户长期记忆。"
    schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": [
                "profile", "preference", "fact", "event", "task", "reminder", "relation"
            ]},
            "content": {"type": "string"},
            "importance": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.6},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["kind", "content"],
    }
    scopes = ["memory.write"]
    is_builtin = True
    cacheable = False

    async def execute(self, *, arguments, ctx, orchestrator):
        rec = await ctx.services.memory.remember(
            user_id=ctx.user_id,
            source_type="explicit",
            source_run_id=ctx.run_id,
            source_session_id=ctx.session_id,
            **arguments,
        )
        return ToolResult(
            call_id="", name=self.name, status="ok",
            content=f"已记住：[{rec.kind}] {rec.content}",
            metadata={"memory_id": rec.memory_id},
        )


class ForgetTool:
    name = "forget"
    description = "删除一条记忆（软删除）。"
    schema = {
        "type": "object",
        "properties": {"memory_id": {"type": "string"}},
        "required": ["memory_id"],
    }
    scopes = ["memory.write"]
    is_builtin = True
    cacheable = False

    async def execute(self, *, arguments, ctx, orchestrator):
        await ctx.services.memory.forget(arguments["memory_id"], ctx.user_id)
        return ToolResult(call_id="", name=self.name, status="ok",
            content="已删除")
```

---

## 8. 测试整合：CLI 模式

为了在没有 Gateway 的情况下端到端验证 Agent 编排层,提供一个 CLI 入口：

```python
# cli/main.py
async def main():
    rt = await Runtime.initialize("config/dev.yaml")

    session_id = SESSION_ID()
    user_id = "test-user"
    await rt._services.session.create(user_id=user_id)

    while True:
        user_input = input(">> ")
        if not user_input.strip():
            break
        run_id = await rt.start_run(
            agent_name="general-agent",
            user_id=user_id, session_id=session_id,
            user_message=user_input,
        )
        # 订阅事件流
        async with rt._bus.subscribe(filter={"run_id": run_id}) as sub:
            async for ev in sub:
                if ev.type == EventType.LLM_TEXT_DELTA:
                    print(ev.payload["text"], end="", flush=True)
                elif ev.type in (EventType.RUN_COMPLETED, EventType.RUN_FAILED):
                    print()
                    break

    await rt.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

这个 CLI 是 Phase 3 的关键验收手段：跑通它意味着整个编排层无 Gateway 也能工作。

---

## 9. 集成测试

```
tests/integration/agent/
├── conftest.py                  # 起 docker-compose + 真实 LLM (可用 mock provider)
├── test_simple_run.py           # 单轮 user → final_answer
├── test_tool_loop.py            # user → tool → tool result → final_answer
├── test_subagent.py             # 主 agent 调 delegate_task → 子 agent 完成
├── test_cancel.py               # 启动 run 后调 cancel,验证级联
├── test_budget_exceeded.py      # token 超限抛异常
└── test_kb_recall_in_context.py # ContextBuilder 真的把 KB 内容带进 prompt
```

---

## 10. 监控埋点

```python
metrics.histogram("agent.run.duration_ms", duration, agent_name=spec.name, status=...)
metrics.counter("agent.run.total", agent_name=spec.name, status=...).inc()
metrics.counter("agent.steps_total", agent_name=spec.name).inc(used_steps)
metrics.gauge("agent.active_runs").inc/dec()
metrics.histogram("agent.subagent_depth", depth)  # 嵌套深度
```

---

## 11. 关键约束与陷阱

1. **不要在 Orchestrator/BaseAgent 中直接捕获并吞掉 CancelledError**——必须让它向上传播
2. **每次 await 之前检查 cancel_event**——尤其是 LLM stream 内部,每收到一个 chunk 就检查一次
3. **SubAgent 不共享父的 cancel_event**——但 RunRegistry 的 cancel 接口会级联调用所有子 Run 的 cancel
4. **run_messages 里的 tool message 必须配对完整**——任何中断都要保证 tool_calls 和 tool 消息对齐,否则下一轮 LLM 会报错
5. **AgentContext.services 是引用而非拷贝**——所有 Run 共享同一组 service 实例,service 自身必须线程安全
6. **agent_registry.resolve 必须 async 安全**——并发请求时不能同时改 cache