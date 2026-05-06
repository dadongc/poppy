from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Protocol

from src.common.types import LLMChunk, PromptPayload


class LLMProvider(Protocol):
    """LLM provider 协议：把各家的流式输出统一为 LLMChunk。"""

    name: str

    async def stream(
        self,
        payload: PromptPayload,
        cancel_event: asyncio.Event,
    ) -> AsyncGenerator[LLMChunk, None]: ...

    def supports(self, model: str) -> bool: ...
