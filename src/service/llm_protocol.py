from __future__ import annotations

from typing import Protocol


class LLMService(Protocol):
    """最小 LLM 接口，Phase 2 服务只依赖此协议。
    Phase 3 的 LLMGateway 将实现此协议。"""

    async def complete_simple(self, prompt: str, *, max_tokens: int = 500) -> str: ...


class StubLLM:
    """测试用 LLM 桩，返回固定占位文本。"""

    async def complete_simple(self, prompt: str, *, max_tokens: int = 500) -> str:
        return "stub summary"
