from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

from src.common.types import (
    AgentContext,
    LLMChunk,
    LLMError,
    PromptPayload,
)

from .llm_circuit_breaker import CircuitBreaker
from .llm_router import ModelRouter

MAX_RETRIES_BEFORE_FIRST_CHUNK = 3


class _FirstChunkFailedError(Exception):
    def __init__(self, error: LLMError) -> None:
        super().__init__(error.message)
        self.error = error


class _StreamMidFailedError(Exception):
    def __init__(self, error: LLMError) -> None:
        super().__init__(error.message)
        self.error = error


class LLMGateway:
    """多模型网关。屏蔽 provider 差异，提供 retry + fallback + circuit breaker。"""

    def __init__(
        self,
        router: ModelRouter,
        circuit_breaker: CircuitBreaker,
    ) -> None:
        self.router = router
        self.cb = circuit_breaker

    async def stream(
        self,
        payload: PromptPayload,
        ctx: AgentContext,
    ) -> AsyncIterator[LLMChunk]:
        models_to_try = [payload.model] + [
            m for m in payload.fallback_models if m != payload.model
        ]
        last_error: LLMError | None = None

        for _attempt, model in enumerate(models_to_try):
            if self.cb.is_open(model):
                continue
            try:
                async for chunk in self._stream_with_retry(payload, model, ctx):
                    yield chunk
                return
            except _FirstChunkFailedError as e:
                last_error = e.error
                self.cb.record_failure(model)
                continue
            except _StreamMidFailedError as e:
                yield LLMChunk(type="error", error=e.error)
                return

        yield LLMChunk(
            type="error",
            error=last_error
            or LLMError(
                type="provider",
                message="all models failed",
                retryable=False,
            ),
        )

    async def _stream_with_retry(
        self,
        payload: PromptPayload,
        model: str,
        ctx: AgentContext,
    ) -> AsyncIterator[LLMChunk]:
        for attempt in range(MAX_RETRIES_BEFORE_FIRST_CHUNK):
            provider, model_spec = self.router.resolve(model)
            first_chunk_seen = False
            try:
                adapted = self._adapt_payload(payload, model, model_spec)
                async for chunk in provider.stream(adapted, ctx.cancel_event):  # type: ignore[attr-defined]
                    first_chunk_seen = True
                    if chunk.type == "usage" and chunk.usage:
                        ctx.used_tokens += chunk.usage.total_tokens
                    yield chunk
                self.cb.record_success(model)
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                err = self._classify_error(e, provider.name)
                if first_chunk_seen:
                    raise _StreamMidFailedError(err) from e
                if not err.retryable or attempt == MAX_RETRIES_BEFORE_FIRST_CHUNK - 1:
                    raise _FirstChunkFailedError(err) from e
                await asyncio.sleep(min(2**attempt, 8))

    def _adapt_payload(
        self, payload: PromptPayload, model: str, model_spec: dict
    ) -> PromptPayload:
        new_payload = replace(payload, model=model)
        new_payload.max_tokens = min(
            payload.max_tokens, model_spec.get("max_output", payload.max_tokens)
        )
        return new_payload

    @staticmethod
    def _classify_error(exc: Exception, provider_name: str) -> LLMError:
        msg = str(exc)
        msg_lower = msg.lower()
        if "rate" in msg_lower or "429" in msg_lower:
            return LLMError(
                type="rate_limit", message=msg, provider=provider_name, retryable=True
            )
        if "context" in msg_lower or "too long" in msg_lower:
            return LLMError(
                type="context_overflow",
                message=msg,
                provider=provider_name,
                retryable=False,
            )
        if "401" in msg or "auth" in msg_lower:
            return LLMError(
                type="auth", message=msg, provider=provider_name, retryable=False
            )
        if isinstance(exc, (asyncio.TimeoutError, ConnectionError)):
            return LLMError(
                type="network", message=msg, provider=provider_name, retryable=True
            )
        return LLMError(
            type="provider", message=msg, provider=provider_name, retryable=False
        )

    def _ensure_providers(self) -> None:
        pass
