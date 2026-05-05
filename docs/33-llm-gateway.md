# 33 · LLMGateway（多模型网关）

> 屏蔽 provider 差异,把 OpenAI / Anthropic / 阿里通义 / 字节豆包等模型的流式输出统一成 **`LLMChunk` 协议**,并提供 retry / fallback / circuit breaker。

实现位置：`src/agent/llm_gateway.py` + `src/agent/llm_providers/`

---

## 1. 设计目标

- **provider 中立**：上层只面对 `LLMChunk` 流,不感知 OpenAI/Anthropic 差异
- **流式优先**：所有调用走 stream,非流式请求是流式的特例（聚合输出）
- **可重试 + fallback**：首 chunk 抵达前可重试或切换 model;首 chunk 后失败不重试
- **可取消**：监听 `cancel_event`,及时关闭 HTTP 连接释放 token
- **可观测**：每段 chunk publish 对应 Event,usage 写入 ctx 累计

---

## 2. 输入输出契约

### 2.1 输入：`PromptPayload`（来自 ContextBuilder）

```python
PromptPayload(
    messages=[{"role": "system", ...}, {"role": "user", ...}, ...],
    tools=[{"type": "function", "function": {...}}, ...],
    model="gpt-4o",
    fallback_models=["claude-3-7-sonnet", "qwen-max"],
    temperature=0.7,
    max_tokens=4096,
    ...
)
```

### 2.2 输出：`AsyncIterator[LLMChunk]`

```python
LLMChunk(type="text_delta", text="hello", ...)
LLMChunk(type="tool_call_start", tool_call_id="...", tool_name="web_search", ...)
LLMChunk(type="tool_call_delta", tool_call_index=0, arguments_delta='{"q":"...', ...)
LLMChunk(type="tool_call_end", tool_call_index=0, arguments_full={...}, ...)
LLMChunk(type="usage", usage=Usage(prompt_tokens=..., completion_tokens=...))
LLMChunk(type="stop", stop_reason="end" | "tool_calls" | "length")
LLMChunk(type="error", error=LLMError(...))
```

---

## 3. 模块结构

```
src/agent/
├── llm_gateway.py              ← 主入口
├── llm_router.py               ← model → provider 路由
├── llm_circuit_breaker.py      ← provider 健康度
├── llm_providers/
│   ├── base.py                 ← LLMProvider Protocol
│   ├── openai.py               ← OpenAI / Azure / 兼容 API
│   ├── anthropic.py
│   ├── dashscope.py            ← 通义千问
│   └── doubao.py               ← 火山方舟豆包
└── llm_catalog.yaml            ← 模型清单（model 名 → provider）
```

---

## 4. Provider Protocol

```python
# src/agent/llm_providers/base.py
from typing import Protocol, AsyncIterator
from src.common.types import PromptPayload, LLMChunk

class LLMProvider(Protocol):
    name: str  # "openai" / "anthropic" / ...

    async def stream(
        self,
        payload: PromptPayload,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[LLMChunk]:
        ...

    def supports(self, model: str) -> bool: ...
```

每个 provider 实现把自家 SDK 的事件流转成 `LLMChunk`。

---

## 5. ModelRouter & Catalog

```yaml
# src/agent/llm_catalog.yaml
models:
  gpt-4o:
    provider: openai
    context_window: 128000
    max_output: 16384
    supports_tools: true
    supports_stream: true
    cost_per_1k_input: 0.005
    cost_per_1k_output: 0.015

  claude-3-7-sonnet:
    provider: anthropic
    context_window: 200000
    max_output: 8192
    supports_tools: true

  qwen-max:
    provider: dashscope
    context_window: 32000
    max_output: 8000

  doubao-pro-128k:
    provider: doubao
    context_window: 128000
    max_output: 4096
```

```python
# src/agent/llm_router.py
class ModelRouter:
    def __init__(self, catalog_path: str, providers: dict[str, LLMProvider]):
        self.catalog = self._load(catalog_path)
        self.providers = providers

    def resolve(self, model: str) -> tuple[LLMProvider, dict]:
        spec = self.catalog["models"].get(model)
        if not spec:
            raise NotFoundError(f"unknown model: {model}")
        provider = self.providers.get(spec["provider"])
        if not provider:
            raise ConfigError(f"provider not configured: {spec['provider']}")
        return provider, spec
```

---

## 6. LLMGateway 主入口

```python
# src/agent/llm_gateway.py
class LLMGateway:
    def __init__(
        self,
        router: ModelRouter,
        circuit_breaker: CircuitBreaker,
        event_bus: EventBus,
    ):
        self.router = router
        self.cb = circuit_breaker
        self.bus = event_bus

    async def stream(
        self,
        payload: PromptPayload,
        ctx: AgentContext,
    ) -> AsyncIterator[LLMChunk]:
        models_to_try = [payload.model, *payload.fallback_models]
        last_error: LLMError | None = None

        for attempt, model in enumerate(models_to_try):
            if self.cb.is_open(model):
                continue
            try:
                async for chunk in self._stream_with_retry(payload, model, ctx):
                    yield chunk
                return  # 成功结束
            except FirstChunkFailedError as e:
                last_error = e.error
                self.cb.record_failure(model)
                continue            # 切下一个 model
            except StreamMidFailedError as e:
                # 已有 chunk 输出,不能切 model（会重复输出）
                yield LLMChunk(type="error", error=e.error)
                return

        # 所有 model 都挂了
        yield LLMChunk(type="error", error=last_error or LLMError(
            type="provider", message="all models failed", retryable=False,
        ))
```

---

## 7. _stream_with_retry：首 chunk 前可重试

```python
MAX_RETRIES_BEFORE_FIRST_CHUNK = 3

async def _stream_with_retry(self, payload, model, ctx):
    """单个 model 内部重试,仅在首 chunk 出现前。"""
    for attempt in range(MAX_RETRIES_BEFORE_FIRST_CHUNK):
        provider, model_spec = self.router.resolve(model)
        first_chunk_seen = False
        try:
            async for chunk in provider.stream(
                self._adapt_payload(payload, model, model_spec), ctx.cancel_event
            ):
                first_chunk_seen = True
                # publish event
                await self._publish_chunk(ctx, chunk)
                # 累计 usage
                if chunk.type == "usage" and chunk.usage:
                    ctx.used_tokens += chunk.usage.total_tokens
                yield chunk
            self.cb.record_success(model)
            return
        except Exception as e:
            err = self._classify_error(e, provider.name)
            if first_chunk_seen:
                # 中途失败,不重试
                raise StreamMidFailedError(err)
            if not err.retryable or attempt == MAX_RETRIES_BEFORE_FIRST_CHUNK - 1:
                raise FirstChunkFailedError(err)
            # 退避
            await asyncio.sleep(min(2 ** attempt, 8))
```

### 错误分类

```python
def _classify_error(self, exc, provider_name) -> LLMError:
    msg = str(exc)
    if "rate" in msg.lower() or "429" in msg:
        return LLMError(type="rate_limit", message=msg, provider=provider_name, retryable=True)
    if "context" in msg.lower() or "too long" in msg.lower():
        return LLMError(type="context_overflow", message=msg, provider=provider_name, retryable=False)
    if "401" in msg or "auth" in msg.lower():
        return LLMError(type="auth", message=msg, provider=provider_name, retryable=False)
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError)):
        return LLMError(type="network", message=msg, provider=provider_name, retryable=True)
    return LLMError(type="provider", message=msg, provider=provider_name, retryable=False)
```

---

## 8. Circuit Breaker

```python
# src/agent/llm_circuit_breaker.py
from collections import defaultdict, deque

class CircuitBreaker:
    """每 model 一个滑动窗口,连续失败 N 次进入 open 状态 cooldown 秒。"""
    def __init__(self, failure_threshold=5, cooldown_sec=60, window_sec=120):
        self.failures: dict[str, deque[float]] = defaultdict(lambda: deque())
        self.opened_at: dict[str, float] = {}
        self.threshold = failure_threshold
        self.cooldown = cooldown_sec
        self.window = window_sec

    def is_open(self, model: str) -> bool:
        opened = self.opened_at.get(model)
        if opened is None:
            return False
        if now_ts() - opened > self.cooldown:
            self.opened_at.pop(model, None)
            self.failures[model].clear()
            return False
        return True

    def record_failure(self, model: str):
        q = self.failures[model]
        now = now_ts()
        q.append(now)
        # 清理过期
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.threshold:
            self.opened_at[model] = now

    def record_success(self, model: str):
        self.failures[model].clear()
        self.opened_at.pop(model, None)
```

---

## 9. Provider 实现示例：OpenAI

```python
# src/agent/llm_providers/openai.py
from openai import AsyncOpenAI

class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, base_url: str = ""):
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
        )

    async def stream(self, payload, cancel_event) -> AsyncIterator[LLMChunk]:
        request = {
            "model": payload.model,
            "messages": payload.messages,
            "tools": payload.tools or None,
            "temperature": payload.temperature,
            "max_tokens": payload.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        stream = await self.client.chat.completions.create(**request)
        # 流式 tool call 拼接缓冲
        tool_buffers: dict[int, dict] = {}

        async for chunk in stream:
            if cancel_event.is_set():
                await stream.close()
                return

            choice = chunk.choices[0] if chunk.choices else None
            if choice:
                delta = choice.delta

                # text
                if delta.content:
                    yield LLMChunk(type="text_delta", text=delta.content)

                # tool calls (incremental)
                for tc in (delta.tool_calls or []):
                    idx = tc.index
                    if idx not in tool_buffers:
                        tool_buffers[idx] = {
                            "id": tc.id or "",
                            "name": tc.function.name or "",
                            "args": "",
                        }
                        yield LLMChunk(
                            type="tool_call_start",
                            tool_call_index=idx,
                            tool_call_id=tc.id or "",
                            tool_name=tc.function.name or "",
                        )
                    if tc.function.arguments:
                        tool_buffers[idx]["args"] += tc.function.arguments
                        yield LLMChunk(
                            type="tool_call_delta",
                            tool_call_index=idx,
                            arguments_delta=tc.function.arguments,
                        )

                # finish
                if choice.finish_reason:
                    # 在 stop 之前先发 tool_call_end
                    for idx, buf in tool_buffers.items():
                        try:
                            full = json.loads(buf["args"]) if buf["args"] else {}
                        except Exception:
                            full = {"_raw": buf["args"]}
                        yield LLMChunk(
                            type="tool_call_end",
                            tool_call_index=idx,
                            tool_call_id=buf["id"],
                            tool_name=buf["name"],
                            arguments_full=full,
                        )
                    yield LLMChunk(
                        type="stop",
                        stop_reason=self._map_stop(choice.finish_reason),
                    )

            # usage（OpenAI 在最后一个 chunk）
            if chunk.usage:
                yield LLMChunk(
                    type="usage",
                    usage=Usage(
                        prompt_tokens=chunk.usage.prompt_tokens,
                        completion_tokens=chunk.usage.completion_tokens,
                        total_tokens=chunk.usage.total_tokens,
                    ),
                )

    def _map_stop(self, reason: str) -> str:
        return {
            "stop": "end",
            "tool_calls": "tool_calls",
            "length": "length",
            "content_filter": "content_filter",
        }.get(reason, "end")
```

### Anthropic 实现要点

- Anthropic 的 message stream 用 `content_block_start/delta/stop` 事件,需要映射为 LLMChunk
- tool_use 的 input 是 partial JSON delta,需要拼接
- system prompt 不在 messages 里,要从 messages 拆出来作为 `system` 顶层参数

### DashScope / Doubao

- 多数兼容 OpenAI 协议（OpenAI-compatible endpoint）,直接复用 OpenAIProvider 改 base_url 即可
- 不兼容的（如旧版 dashscope 原生协议）单独写 provider

---

## 10. Payload Adaptation

不同 provider 对 messages 格式有差异,`_adapt_payload` 负责微调：

```python
def _adapt_payload(self, payload: PromptPayload, model, model_spec) -> PromptPayload:
    new_payload = replace(payload, model=model)

    # Anthropic 要求 system 拆出
    if model_spec["provider"] == "anthropic":
        system_msgs = [m["content"] for m in payload.messages if m["role"] == "system"]
        non_system = [m for m in payload.messages if m["role"] != "system"]
        new_payload.messages = non_system
        new_payload.metadata = {"system": "\n\n".join(system_msgs)}

    # max_tokens 不能超 model 上限
    new_payload.max_tokens = min(payload.max_tokens, model_spec.get("max_output", payload.max_tokens))
    return new_payload
```

---

## 11. Cancel 取消

每个 provider 在 stream 内部 check `cancel_event.is_set()`：

```python
async for chunk in stream:
    if cancel_event.is_set():
        await stream.close()    # 关闭底层 HTTP,释放 token
        yield LLMChunk(type="error", error=LLMError(
            type="cancelled", message="cancelled by user", retryable=False,
        ))
        return
```

LLMGateway 上层不需要再处理,cancel 路径已经在 provider 内闭合。

---

## 12. 事件 publish

```python
async def _publish_chunk(self, ctx, chunk: LLMChunk):
    event_type = {
        "text_delta": EventType.LLM_TEXT_DELTA,
        "tool_call_start": EventType.LLM_TOOL_CALL_START,
        "tool_call_delta": EventType.LLM_TOOL_CALL_DELTA,
        "tool_call_end": EventType.LLM_TOOL_CALL_END,
        "usage": EventType.LLM_USAGE,
        "stop": EventType.LLM_STOP,
        "error": EventType.LLM_ERROR,
    }[chunk.type]

    payload = {"chunk_type": chunk.type}
    if chunk.text:
        payload["text"] = chunk.text
    if chunk.tool_call_id:
        payload["tool_call_id"] = chunk.tool_call_id
    if chunk.usage:
        payload["usage"] = asdict(chunk.usage)

    await self.bus.publish(Event(
        event_id=EVENT_ID(),
        type=event_type,
        run_id=ctx.run_id,
        parent_run_id=ctx.parent_run_id,
        session_id=ctx.session_id,
        user_id=ctx.user_id,
        ts=now_ts(),
        payload=payload,
    ))
```

---

## 13. 单元测试

```
tests/unit/agent/llm_gateway/
├── test_router_resolve.py            # model 名映射 provider
├── test_circuit_breaker.py           # 失败累积 → open;冷却后 close
├── test_first_chunk_retry.py         # mock provider 第一次抛 → 重试
├── test_mid_stream_failure.py        # 已 yield → 不重试,转 error chunk
├── test_fallback_chain.py            # primary 失败 → fallback model 接管
├── test_cancel_releases_stream.py    # cancel_event 触发 close 调用
├── test_usage_aggregation.py         # ctx.used_tokens 正确累加
└── providers/
    ├── test_openai_chunks.py         # 喂 mock raw event → 期望 LLMChunk 序列
    └── test_anthropic_chunks.py
```

### 关键测试

```python
@pytest.mark.asyncio
async def test_first_chunk_retry_then_succeed():
    provider = MagicMock()
    call_count = 0
    async def stream_func(payload, cancel):
        nonlocal call_count; call_count += 1
        if call_count == 1:
            raise ConnectionError("transient")
        yield LLMChunk(type="text_delta", text="hello")
        yield LLMChunk(type="stop", stop_reason="end")
    provider.stream = stream_func

    gateway = LLMGateway(...)
    chunks = [c async for c in gateway.stream(payload, ctx)]
    assert call_count == 2
    assert any(c.type == "text_delta" for c in chunks)

@pytest.mark.asyncio
async def test_mid_stream_failure_no_retry():
    """已经有 text_delta 出来后再抛错,不能重试。"""
    ...
```

---

## 14. 与其他模块的契约

| 调用方 | 调用点 | 入参 | 出参 |
|---|---|---|---|
| BaseAgent._stream_llm | plan 阶段 | `PromptPayload, ctx` | `AsyncIterator[LLMChunk]` |
| ContextBuilder | 输出 payload 给 gateway | - | - |
| EventBus | publish chunk 事件 | `Event` | None |

---

## 15. 设计 FAQ

**Q: 为什么 LLMGateway 不做 prompt 模板?**
A: prompt 组装是 ContextBuilder 的职责。Gateway 只做 IO 和协议转换,单一职责更易测。

**Q: 为什么不用 LiteLLM 现成的封装?**
A: LiteLLM 抽象太重,bug 修复滞后,且流式 + tool call 的细节支持参差不齐。手写 provider 适配 200 行就够。

**Q: 阿里通义和豆包都说兼容 OpenAI,为什么还要单独 provider?**
A: 大多数情况下确实复用 OpenAIProvider 就行（只换 base_url + api_key）。"单独 provider"是为了**少数不兼容字段**（如 dashscope 原生 API 的 input_messages 格式）。如果用 compatible endpoint,配置一行即可。