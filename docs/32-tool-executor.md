# 32 · ToolExecutor（工具执行器）

> 把 LLM 输出的 `ToolCall[]` 安全、并发、可观测地执行掉,输出 `ToolResult[]` 喂回下一轮。

实现位置：`src/agent/tool_executor.py`

---

## 1. 设计目标

- **安全**：每个工具调用都过权限校验、参数校验、scope 隔离
- **可控**：并发上限、超时、取消传播;失败工具不阻塞其他工具
- **幂等**：相同输入相同输出（按需）;重试不产生副作用
- **可观测**：每步发布 Event;ToolResult 携带耗时、错误、artifact 引用
- **大输出友好**：超长输出自动转 Artifact,仅注入摘要给 LLM

---

## 2. 八步 Pipeline

```
ToolCall 进入
   ↓
[1] permission_check    校验 user_id 是否被允许调用此 tool（scope）
   ↓
[2] tool_lookup         从 ToolRegistry 查找 Tool 实例
   ↓
[3] schema_validate     用 JSON Schema 校验 arguments
   ↓
[4] context_inject      注入 AgentContext / Services（工具不接触全局状态）
   ↓
[5] idempotent_check    若工具声明 cacheable,查缓存命中
   ↓
[6] execute             实际调用 tool.execute(),受 Semaphore + timeout 限制
   ↓
[7] post_process        大输出转 Artifact、生成 summary
   ↓
[8] cache_write         若 cacheable,写入结果缓存
   ↓
ToolResult 输出
```

---

## 3. 数据结构

回顾 `01-common-types.md` 中的：

```python
ToolCall(call_id, name, arguments, arguments_raw)
ToolResult(call_id, name, status, content, artifact_id, error_type, error_message, duration_ms, metadata)
ExecutionReport(results, total_duration_ms, parallel_count, failed_count)
```

并新增：

```python
@dataclass(slots=True, kw_only=True)
class ToolInvocation:
    """ToolExecutor 内部用：一次调用的完整上下文。"""
    call: ToolCall
    tool: "Tool"
    started_at: float = 0.0
    ended_at: float = 0.0
    cache_key: str | None = None
    cache_hit: bool = False
```

---

## 4. ToolExecutor 接口

```python
import asyncio
from src.common.types import ToolCall, ToolResult, ExecutionReport, AgentContext

class ToolExecutor:
    def __init__(self, ctx: AgentContext, orchestrator: "Orchestrator"):
        self.ctx = ctx
        self.orch = orchestrator
        self.services = ctx.services
        self.semaphore = asyncio.Semaphore(ctx.spec.max_parallel_tools)

    async def execute(self, calls: list[ToolCall]) -> ExecutionReport:
        """并发执行一批工具调用。失败不影响其他工具。"""
        started = now_ts()
        tasks = [asyncio.create_task(self._execute_one(c)) for c in calls]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        # _execute_one 内部已捕获所有异常,不会抛出
        return ExecutionReport(
            results=results,
            total_duration_ms=int((now_ts() - started) * 1000),
            parallel_count=len(calls),
            failed_count=sum(1 for r in results if r.status != "ok"),
        )

    async def _execute_one(self, call: ToolCall) -> ToolResult:
        async with self.semaphore:
            return await self._pipeline(call)
```

---

## 5. 完整 Pipeline 实现

```python
async def _pipeline(self, call: ToolCall) -> ToolResult:
    inv = ToolInvocation(call=call, tool=None, started_at=now_ts())  # type: ignore
    await self._publish(EventType.TOOL_STARTED, call=call)

    try:
        # [1] permission
        await self._check_permission(call)

        # [2] lookup
        tool = self._lookup(call.name)
        inv.tool = tool

        # [3] validate schema
        self._validate_schema(tool, call.arguments)

        # [4] context inject — 通过 tool.execute(ctx, args) 签名传入,不需要单独步骤
        # [5] idempotent cache lookup
        if tool.cacheable:
            inv.cache_key = self._make_cache_key(tool, call.arguments)
            cached = await self.services.cache.get(inv.cache_key)
            if cached is not None:
                inv.cache_hit = True
                return self._finalize(inv, status="ok", raw_output=cached, from_cache=True)

        # [6] execute
        raw_output = await self._execute_with_timeout(tool, call)

        # [7] post-process (artifact化)
        result = await self._post_process(inv, raw_output)

        # [8] cache write
        if tool.cacheable and result.status == "ok":
            await self.services.cache.set(
                inv.cache_key, raw_output, ttl=tool.cache_ttl
            )

        return result

    except PermissionError as e:
        return self._finalize(inv, status="denied", error=e)
    except SchemaValidationError as e:
        return self._finalize(inv, status="error", error=e, error_type="invalid_args")
    except asyncio.TimeoutError as e:
        return self._finalize(inv, status="timeout", error=e)
    except asyncio.CancelledError:
        return self._finalize(inv, status="cancelled")
    except Exception as e:
        # 未知异常落 error,不抛出;记录堆栈
        logger.exception("tool_execute_failed", tool=call.name, call_id=call.call_id)
        return self._finalize(inv, status="error", error=e, error_type="unknown")
    finally:
        inv.ended_at = now_ts()
        await self._publish(
            EventType.TOOL_COMPLETED,
            call=call,
            duration_ms=int((inv.ended_at - inv.started_at) * 1000),
            cache_hit=inv.cache_hit,
        )
```

---

## 6. 各步骤细节

### 6.1 [1] Permission Check

```python
async def _check_permission(self, call: ToolCall):
    # 6.1.1 spec 级白名单（agent 是否被授权用此工具）
    if call.name not in self.ctx.spec.allowed_tools and not self._is_builtin(call.name):
        raise PermissionError(f"agent '{self.ctx.spec.name}' not allowed to call '{call.name}'")

    # 6.1.2 用户级 scope（OAuth 工具特有）
    tool = self.services.tool.get(call.name)
    if tool and tool.scopes:
        granted = await self.services.auth.get_user_scopes(self.ctx.user_id)
        missing = set(tool.scopes) - set(granted)
        if missing:
            raise PermissionError(f"user lacks scopes: {missing}")
```

### 6.2 [2] Tool Lookup

```python
def _lookup(self, name: str) -> "Tool":
    tool = self.services.tool.get(name)
    if not tool:
        raise NotFoundError(f"tool not found: {name}")
    return tool
```

### 6.3 [3] Schema Validate

```python
import jsonschema

def _validate_schema(self, tool, args: dict):
    try:
        jsonschema.validate(args, tool.schema)
    except jsonschema.ValidationError as e:
        raise SchemaValidationError(f"invalid args for {tool.name}: {e.message}")
```

### 6.4 [4] Context Inject

工具签名约定：

```python
class Tool(Protocol):
    async def execute(self, ctx: AgentContext, args: dict) -> Any: ...
```

Tool 内部可通过 `ctx.services.xxx` 访问任何服务,**禁止直接 import 全局单例**。

### 6.5 [5] Idempotent Cache Key

```python
import hashlib, json

def _make_cache_key(self, tool, args: dict) -> str:
    # 按 user 隔离 + tool name + args 规范化哈希
    norm = json.dumps(args, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(f"{tool.name}|{norm}".encode()).hexdigest()[:16]
    return f"toolcache:{self.ctx.user_id}:{tool.name}:{h}"
```

工具自己声明 `cacheable=True` + `cache_ttl=300`（秒）。默认不缓存。

### 6.6 [6] Execute with Timeout & Cancel

```python
async def _execute_with_timeout(self, tool, call: ToolCall):
    # 工具自己可声明 timeout,否则用全局默认
    timeout = getattr(tool, "timeout_sec", 60)
    remaining = self._remaining_deadline()
    actual_timeout = min(timeout, remaining) if remaining > 0 else timeout

    # 同时监听 cancel_event
    exec_coro = tool.execute(self.ctx, call.arguments)
    cancel_coro = self.ctx.cancel_event.wait()
    done, pending = await asyncio.wait(
        [asyncio.create_task(exec_coro), asyncio.create_task(cancel_coro)],
        timeout=actual_timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()

    if not done:
        raise asyncio.TimeoutError(f"tool {tool.name} timeout after {actual_timeout}s")

    finished = done.pop()
    if finished.get_coro() is cancel_coro:
        raise asyncio.CancelledError()
    return finished.result()
```

### 6.7 [7] Post-process（大输出 Artifact 化）

```python
LLM_INJECT_LIMIT = 4000  # 字符级阈值

async def _post_process(self, inv, raw_output) -> ToolResult:
    text = self._stringify(raw_output)

    if len(text) <= LLM_INJECT_LIMIT:
        return self._finalize(inv, status="ok", raw_output=raw_output, content=text)

    # 大输出转 artifact
    artifact = await self.services.artifact.save(
        content=text,
        user_id=self.ctx.user_id,
        source_type="tool_output",
        source_run_id=self.ctx.run_id,
        source_session_id=self.ctx.session_id,
        source_tool_name=inv.tool.name,
        source_call_id=inv.call.call_id,
        title=f"{inv.tool.name} output",
    )
    summary = await self.services.artifact.summarize(artifact, target_tokens=600)
    inject_content = (
        f"<artifact id=\"{artifact.artifact_id}\" mime=\"{artifact.mime_type}\" "
        f"size=\"{artifact.size_bytes}\">\n{summary}\n</artifact>"
    )
    return self._finalize(
        inv, status="ok", raw_output=raw_output,
        content=inject_content, artifact_id=artifact.artifact_id,
    )

def _stringify(self, output) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, (dict, list)):
        return json.dumps(output, ensure_ascii=False, indent=2)
    return str(output)
```

### 6.8 [8] Cache Write — 如上 §5 已展示

---

## 7. _finalize 收口

```python
def _finalize(self, inv, *, status, raw_output=None, content=None,
              artifact_id=None, error=None, error_type=None, from_cache=False) -> ToolResult:
    duration_ms = int((now_ts() - inv.started_at) * 1000)
    return ToolResult(
        call_id=inv.call.call_id,
        name=inv.call.name,
        status=status,
        content=content or (self._stringify(raw_output) if raw_output is not None else ""),
        artifact_id=artifact_id,
        error_type=error_type or (type(error).__name__ if error else None),
        error_message=str(error) if error else None,
        duration_ms=duration_ms,
        metadata={"cache_hit": inv.cache_hit, "from_cache": from_cache},
    )
```

---

## 8. 取消传播

- `ctx.cancel_event` 由 RunRegistry 在收到 `POST /runs/{id}/cancel` 时 `.set()`
- 每个工具执行内部 `asyncio.wait` 同时监听 cancel + 业务 coro
- 工具自己若调用网络 IO,应在 IO 上加 `asyncio.wait_for` 或在循环内 `if cancel_event.is_set(): raise`
- 取消语义是**软取消**：工具应尽快返回,但不强行 kill

---

## 9. 并发控制

```python
self.semaphore = asyncio.Semaphore(ctx.spec.max_parallel_tools)  # 默认 3
```

- 同一步内的多个 ToolCall 受 semaphore 限制
- 不同 step 间不共享（每次 `execute` 新的 semaphore?不!应在 ctx 持有,跨步不重置）

修正：

```python
# AgentContext 持有 semaphore
@dataclass(slots=True, kw_only=True)
class AgentContext:
    ...
    tool_semaphore: asyncio.Semaphore = field(default=None)

# Runtime 启动 run 时初始化
ctx.tool_semaphore = asyncio.Semaphore(ctx.spec.max_parallel_tools)
```

---

## 10. 事件发布

每步 publish 一条 Event：

```python
async def _publish(self, event_type, *, call, **payload):
    await self.services.event_bus.publish(Event(
        event_id=EVENT_ID(),
        type=event_type,
        run_id=self.ctx.run_id,
        parent_run_id=self.ctx.parent_run_id,
        session_id=self.ctx.session_id,
        user_id=self.ctx.user_id,
        ts=now_ts(),
        payload={
            "call_id": call.call_id,
            "tool_name": call.name,
            "arguments": call.arguments,
            **payload,
        },
    ))
```

---

## 11. 与 SubAgent 工具的衔接

`delegate_task` 是一个特殊工具：它的 `execute()` 内部调 `orchestrator.spawn_subagent()`,结果是子 run 的最终输出。

```python
class DelegateTaskTool:
    name = "delegate_task"
    cacheable = False  # SubAgent 永不缓存

    async def execute(self, ctx, args):
        agent_name = args["agent_name"]
        task = args["task"]
        sub_ctx = await ctx.services.runtime.orchestrator(ctx.run_id).spawn_subagent(
            agent_name=agent_name, task=task,
        )
        return await sub_ctx.wait_result()  # 阻塞直到子 run 终态
```

ToolExecutor 不需要为 SubAgent 做特殊处理,普通 pipeline 就能跑。

---

## 12. 单元测试

```
tests/unit/agent/tool_executor/
├── test_permission_deny.py       # spec 不含工具 → denied
├── test_schema_invalid.py        # args 缺字段 → error.invalid_args
├── test_timeout.py               # 慢工具被中断
├── test_cancel_propagation.py    # cancel_event 触发即返回 cancelled
├── test_parallel_semaphore.py    # 并发 5 个工具,max_parallel_tools=2 → 仅 2 同时跑
├── test_idempotent_cache.py      # 第二次相同调用直接返回缓存
├── test_artifact_fallback.py     # 大输出转 artifact,content 是引用块
├── test_failure_isolation.py     # 5 工具中 1 个抛错,其他正常完成
└── test_event_publishing.py      # tool.started + tool.completed 数量
```

### 关键测试

```python
@pytest.mark.asyncio
async def test_failure_isolation():
    """一个工具失败不应阻塞其他工具。"""
    calls = [ToolCall(...) for _ in range(5)]
    # 设第 2 个工具 mock 抛 ValueError
    report = await executor.execute(calls)
    assert len(report.results) == 5
    assert report.failed_count == 1
    assert report.results[1].status == "error"
    assert all(r.status == "ok" for r in report.results[:1] + report.results[2:])

@pytest.mark.asyncio
async def test_cancel_during_execution():
    async def slow_tool(ctx, args):
        await asyncio.sleep(10)
    register_tool("slow", slow_tool)
    task = asyncio.create_task(executor.execute([ToolCall(name="slow", ...)]))
    await asyncio.sleep(0.1)
    ctx.cancel_event.set()
    report = await task
    assert report.results[0].status == "cancelled"
```

---

## 13. 与其他模块的契约

| 调用方 | 调用点 | 入参 | 出参 |
|---|---|---|---|
| BaseAgent.run_loop | act 阶段 | `list[ToolCall]` | `ExecutionReport` |
| ToolRegistry | 内部 lookup | `name` | `Tool` 实例 |
| ArtifactStore | post-process 大输出 | `content, user_id, source...` | `Artifact` |
| Cache | idempotent 缓存 | `key, value, ttl` | bytes / None |
| EventBus | publish | `Event` | None |

---

## 14. 设计 FAQ

**Q: 为什么不在工具失败时抛 ToolError 中断 ReAct?**
A: ReAct 的本意是让模型看到失败原因后**自主决定**重试或换策略。强行中断剥夺了模型的自我修复能力。

**Q: 为什么 Artifact 化阈值是 4000 字符而不是 token?**
A: 阈值粗略即可,character count 简单且无需 encoder。阈值的目的是"避免 prompt 暴涨",精度不重要。

**Q: 为什么 cacheable 默认 False?**
A: 大多数工具有副作用（发邮件、写日历）,缓存会引发幂等问题。只有纯查询类工具（web_search, weather）才显式开启。