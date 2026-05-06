from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from src.common.types import LLMChunk, LLMError, PromptPayload, Usage


class OpenAiProvider:
    """OpenAI-compatible provider — 覆盖 OpenAI / DeepSeek / 豆包 / 通义等。"""

    name = "openai"

    def __init__(
        self, api_key: str, base_url: str = "", model: str | None = None
    ) -> None:
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url or None,
        )
        self._model = model

    def supports(self, model: str) -> bool:
        return True  # OpenAI-compatible 通常支持所有 model

    async def stream(
        self,
        payload: PromptPayload,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[LLMChunk]:
        model = payload.model or self._model or ""
        request: dict = {
            "model": model,
            "messages": payload.messages,
            "tools": payload.tools or None,
            "temperature": payload.temperature,
            "max_tokens": payload.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        # Use raw httpx client to support proper cancellation
        try:
            stream = await self.client.chat.completions.create(**request)
        except Exception as e:
            yield LLMChunk(
                type="error",
                error=LLMError(
                    type="provider",
                    message=str(e),
                    provider=self.name,
                    retryable=_is_retryable(e),
                ),
            )
            return

        tool_buffers: dict[int, dict] = {}

        try:
            async for chunk in stream:
                if cancel_event.is_set():
                    try:
                        await stream.close()
                    except Exception:
                        pass
                    yield LLMChunk(
                        type="error",
                        error=LLMError(
                            type="provider",
                            message="cancelled by user",
                            provider=self.name,
                            retryable=False,
                        ),
                    )
                    return

                choice = chunk.choices[0] if chunk.choices else None
                if choice:
                    delta = choice.delta

                    # Text delta
                    if delta.content:
                        yield LLMChunk(type="text_delta", text=delta.content)

                    # Tool calls (incremental)
                    for tc in delta.tool_calls or []:
                        idx = tc.index
                        if idx not in tool_buffers:
                            tool_buffers[idx] = {
                                "id": tc.id or "",
                                "name": tc.function.name if tc.function else "",
                                "args": "",
                            }
                            yield LLMChunk(
                                type="tool_call_start",
                                tool_call_index=idx,
                                tool_call_id=tc.id or "",
                                tool_name=tc.function.name if tc.function else "",
                            )
                        if tc.function and tc.function.arguments:
                            tool_buffers[idx]["args"] += tc.function.arguments
                            yield LLMChunk(
                                type="tool_call_delta",
                                tool_call_index=idx,
                                arguments_delta=tc.function.arguments,
                            )

                    # Finish
                    if choice.finish_reason:
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
                            stop_reason=_map_stop(choice.finish_reason),  # type: ignore[arg-type]
                        )

                if chunk.usage:
                    yield LLMChunk(
                        type="usage",
                        usage=Usage(
                            prompt_tokens=chunk.usage.prompt_tokens or 0,
                            completion_tokens=chunk.usage.completion_tokens or 0,
                            total_tokens=chunk.usage.total_tokens or 0,
                        ),
                    )
        except asyncio.CancelledError:
            try:
                await stream.close()
            except Exception:
                pass
            raise
        except Exception as e:
            yield LLMChunk(
                type="error",
                error=LLMError(
                    type="provider",
                    message=str(e),
                    provider=self.name,
                    retryable=_is_retryable(e),
                ),
            )


def _map_stop(reason: str) -> str:
    mapping = {
        "stop": "end",
        "tool_calls": "tool_calls",
        "length": "length",
        "content_filter": "content_filter",
    }
    return mapping.get(reason, "end")


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    if "rate" in msg or "429" in msg:
        return True
    if "timeout" in msg or "connect" in msg:
        return True
    return False
