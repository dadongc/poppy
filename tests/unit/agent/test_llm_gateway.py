from __future__ import annotations

import asyncio

import pytest

from src.agent.llm_gateway import LLMGateway
from src.common.clock import now_ts
from src.common.ids import new_id
from src.common.types import (
    AgentContext,
    AgentSpec,
    LLMChunk,
    Message,
    PromptPayload,
    Services,
)


class StubProvider:
    """Provider stub that can be configured to succeed or fail."""

    def __init__(self, name="stub", chunks=None, fail_before_first=False, fail_mid=False):
        self.name = name
        self._chunks = chunks or [
            LLMChunk(type="text_delta", text="Hello"),
            LLMChunk(type="stop", stop_reason="end"),
        ]
        self._fail_before_first = fail_before_first
        self._fail_mid = fail_mid
        self.call_count = 0

    async def stream(self, payload, cancel_event):
        self.call_count += 1
        if self._fail_before_first:
            raise ConnectionError("connection refused")
        for c in self._chunks:
            if self._fail_mid and c.type == "text_delta":
                yield c
                raise ConnectionError("mid-stream failure")
            yield c

    def supports(self, model):
        return True


class StubRouter:
    """Router stub that returns a configured provider."""

    def __init__(self, provider, spec=None):
        self.provider = provider
        self.default_spec = spec or {"max_output": 4096}

    def resolve(self, model):
        return self.provider, self.default_spec


class NeverOpenBreaker:
    def is_open(self, model):
        return False

    def record_failure(self, model):
        pass

    def record_success(self, model):
        pass


class AlwaysOpenBreaker:
    def is_open(self, model):
        return True

    def record_failure(self, model):
        pass

    def record_success(self, model):
        pass


def _agent_ctx():
    return AgentContext(
        run_id=new_id("run"),
        session_id=new_id("ses"),
        user_id="test-user",
        trace_id=new_id("trc"),
        spec=AgentSpec(
            name="test",
            allowed_tools={"final_answer"},
            max_steps=10,
            token_budget=50000,
            deadline_sec=300,
        ),
        user_message=Message(role="user", content="hi", created_at=now_ts()),
        cancel_event=asyncio.Event(),
        deadline_at=now_ts() + 300,
        started_at=now_ts(),
        services=Services(),
    )


def _payload():
    return PromptPayload(
        messages=[{"role": "user", "content": "hi"}],
        model="deepseek-chat",
        fallback_models=["gpt-4o-mini"],
        token_estimate=100,
        sections={"current": 50},
        temperature=0.7,
        max_tokens=4096,
    )


class TestLLMGateway:
    @pytest.mark.asyncio
    async def test_successful_stream(self):
        provider = StubProvider()
        gateway = LLMGateway(
            router=StubRouter(provider),
            circuit_breaker=NeverOpenBreaker(),
        )
        chunks = []
        async for c in gateway.stream(_payload(), _agent_ctx()):
            chunks.append(c)
        assert len(chunks) == 2
        assert provider.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_before_first_chunk(self):
        provider = StubProvider()
        provider._fail_before_first = True
        gateway = LLMGateway(
            router=StubRouter(provider),
            circuit_breaker=NeverOpenBreaker(),
        )
        chunks = []
        async for c in gateway.stream(_payload(), _agent_ctx()):
            chunks.append(c)
        # Should have gotten an error after all retries exhausted
        assert chunks[0].type == "error"

    @pytest.mark.asyncio
    async def test_mid_stream_no_retry(self):
        provider = StubProvider(fail_mid=True)
        gateway = LLMGateway(
            router=StubRouter(provider),
            circuit_breaker=NeverOpenBreaker(),
        )
        chunks = []
        async for c in gateway.stream(_payload(), _agent_ctx()):
            chunks.append(c)
        # Mid-stream failure: should yield text_delta then error
        assert chunks[0].type == "text_delta"
        assert any(c.type == "error" for c in chunks)

    @pytest.mark.asyncio
    async def test_circuit_breaker_skip(self):
        provider = StubProvider()
        gateway = LLMGateway(
            router=StubRouter(provider),
            circuit_breaker=AlwaysOpenBreaker(),
        )
        chunks = []
        async for c in gateway.stream(_payload(), _agent_ctx()):
            chunks.append(c)
        # Model is skipped, fallback also skipped (same model)
        assert chunks[0].type == "error"
        assert provider.call_count == 0

    @pytest.mark.asyncio
    async def test_fallback(self):
        fail_provider = StubProvider(name="primary")
        fail_provider._fail_before_first = True
        ok_provider = StubProvider(name="fallback")

        class SelectiveRouter:
            def __init__(self):
                self.call = 0
            def resolve(self, model):
                self.call += 1
                if model == "deepseek-chat":
                    return fail_provider, {"max_output": 4096}
                return ok_provider, {"max_output": 4096}

        gateway = LLMGateway(
            router=SelectiveRouter(),
            circuit_breaker=NeverOpenBreaker(),
        )
        chunks = []
        async for c in gateway.stream(_payload(), _agent_ctx()):
            chunks.append(c)
        types = [c.type for c in chunks]
        assert "text_delta" in types
        # Primary failed, fallback succeeded
        assert ok_provider.call_count == 1

    @pytest.mark.asyncio
    async def test_error_classification_rate_limit(self):
        err = LLMGateway._classify_error(Exception("429 rate limit exceeded"), "test")
        assert err.type == "rate_limit"
        assert err.retryable is True

    @pytest.mark.asyncio
    async def test_error_classification_auth(self):
        err = LLMGateway._classify_error(Exception("401 auth error"), "test")
        assert err.type == "auth"
        assert err.retryable is False

    @pytest.mark.asyncio
    async def test_error_classification_network(self):
        err = LLMGateway._classify_error(ConnectionError("reset"), "test")
        assert err.type == "network"
        assert err.retryable is True
