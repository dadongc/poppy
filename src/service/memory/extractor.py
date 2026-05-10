from __future__ import annotations

import json

from src.service.llm_protocol import LLMService


class MemoryExtractor:
    EXTRACT_PROMPT = """从用户对话摘要中提取应记忆的事实。
输出 JSON 数组：{{kind, content, importance, confidence}}
- kind: profile/preference/fact/event/task/reminder
- content: 第三人称陈述句
- importance/confidence: 0~1
只提取有保留价值的。

摘要：
{summary}"""

    def __init__(self, llm: LLMService) -> None:
        self._llm = llm

    async def extract(self, summary: str) -> list[dict]:
        raw = await self._llm.complete_simple(
            self.EXTRACT_PROMPT.format(summary=summary), max_tokens=1000
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []
