from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.common.types import PromptPayload, RetrievalQuery

if TYPE_CHECKING:
    from src.common.types import AgentContext, Message

DEFAULT_SYSTEM_PROMPT = """你是 {agent_name}，一个尽职、严谨、可解释的 AI 助理。
- 优先使用工具获取真实信息，不要编造
- 必要时调用 final_answer 工具结束本轮
- 回答用中文（除非用户用其他语言）
当前时间：{datetime}"""


class TokenEstimator:
    """简单 token 估算。字符级，CJK 字符 1.5 token/字，ASCII 0.3 token/字。"""

    @staticmethod
    def estimate(text: str) -> int:
        cjk = sum(1 for c in text if ord(c) > 0x2000)
        ascii_n = len(text) - cjk
        return int(cjk * 1.5 + ascii_n * 0.3)

    @classmethod
    def estimate_messages(cls, messages: list[dict]) -> int:
        return sum(cls.estimate(m.get("content", "")) + 4 for m in messages)


class ContextBuilder:
    """上下文组装器。把分散在各 Service 的状态组装成 PromptPayload。"""

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx
        self.services = ctx.services
        self.spec = ctx.spec
        self.estimator = TokenEstimator()

    async def build(self, run_messages: list[Message]) -> PromptPayload:
        budgets = self._allocate_budget()

        # Concurrent IO
        history_task = self._build_history(budgets["history"])
        memory_task = self._build_memory(budgets["memory"])
        kb_task = self._build_kb(budgets["kb"])

        history, memory, kb = await asyncio.gather(
            history_task, memory_task, kb_task
        )

        role = self._build_role()
        manifest = self._build_manifest()
        env = self._build_env()
        current = self._build_current(run_messages)

        messages, sections, dropped = self._assemble(
            role, manifest, env, memory, kb, history, current,
            total_budget=self._total_input_budget(),
        )

        return PromptPayload(
            messages=messages,
            tools=self._render_tools(),
            model=self.spec.preferred_model if self.spec else "",
            fallback_models=self.spec.fallback_models if self.spec else [],
            temperature=self.spec.temperature if self.spec else 0.7,
            max_tokens=self.spec.max_tokens if self.spec else 4096,
            token_estimate=sum(sections.values()),
            sections=sections,
            dropped=dropped,
        )

    def _allocate_budget(self) -> dict[str, int]:
        spec = self.spec
        total = spec.token_budget if spec else 50000
        output_reserved = spec.max_tokens if spec else 4096
        input_budget = int((total - output_reserved) * 0.9)

        role = 1500
        manifest = 3000
        env = 500
        current = 8000

        remaining = input_budget - role - manifest - env - current
        memory_b = int(remaining * 0.20)
        kb_b = int(remaining * 0.30)
        history_b = remaining - memory_b - kb_b

        return {
            "role": role, "manifest": manifest, "env": env,
            "memory": memory_b, "kb": kb_b,
            "history": max(history_b, 100), "current": current,
        }

    def _total_input_budget(self) -> int:
        spec = self.spec
        total = spec.token_budget if spec else 50000
        output_reserved = spec.max_tokens if spec else 4096
        return int((total - output_reserved) * 0.9)

    # --- Section builders ---

    def _build_role(self) -> str:
        template = (self.spec.system_prompt if self.spec and self.spec.system_prompt
                    else DEFAULT_SYSTEM_PROMPT)
        now_str = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        return template.format(
            agent_name=self.spec.name if self.spec else "Poppy",
            user_id=self.ctx.user_id,
            datetime=now_str,
        )

    def _build_manifest(self) -> str:
        tool_svc = self.services.tool
        lines = ["## 可用工具"]
        if tool_svc and self.spec:
            tools = tool_svc.list_for_agent(self.spec)
            for t in tools:
                lines.append(f"- **{t.name}**: {t.description}")

        skill_svc = self.services.skill
        loaded = self.ctx.extra_inputs.get("loaded_skills", [])
        if loaded and skill_svc:
            lines.append("")
            lines.append("## 已加载技能")
            for s in loaded[:3]:
                name = s.name if hasattr(s, "name") else str(s)
                content = s.content if hasattr(s, "content") else ""
                lines.append(f"### {name}\n{content[:2000]}")
        return "\n".join(lines)

    def _build_env(self) -> str:
        now_str = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        spec = self.spec
        return f"""## 运行环境
- 当前时间: {now_str}
- 时区: {self.ctx.extra_inputs.get('tz', 'Asia/Shanghai')}
- 用户 ID: {self.ctx.user_id}
- Session: {self.ctx.session_id}
- Run: {self.ctx.run_id}
- 步数: {self.ctx.used_steps}/{spec.max_steps if spec else '?'}
- 已用 tokens: {self.ctx.used_tokens}/{spec.token_budget if spec else '?'}"""

    async def _build_memory(self, budget: int) -> str:
        if budget <= 0:
            return ""
        query = self._derive_query()
        if not query:
            return ""

        memory_svc = self.services.memory
        if memory_svc is None:
            return ""

        try:
            hits = await memory_svc.recall(
                user_id=self.ctx.user_id,
                query=query,
                top_k=10,
                diversify=True,
            )
        except Exception:
            return ""

        out, used = [], 0
        for h in hits:
            kind = h.metadata.get("kind", "note") if hasattr(h, "metadata") else "note"
            text = h.content if hasattr(h, "content") else str(h)
            line = f"- [{kind}] {text}"
            cost = self.estimator.estimate(line)
            if used + cost > budget:
                break
            out.append(line)
            used += cost
        return f"<memory>\n{chr(10).join(out)}\n</memory>" if out else ""

    async def _build_kb(self, budget: int) -> str:
        if budget <= 0:
            return ""
        query = self._derive_query()
        if not query:
            return ""

        retriever = self.services.retriever
        if retriever is None:
            return ""

        try:
            hits = await retriever.search(RetrievalQuery(
                text=query, user_id=self.ctx.user_id,
                channels=["kb"], top_k=8, diversify=True,
            ))
        except Exception:
            return ""

        blocks, used = [], 0
        for h in hits:
            cite = h.citation if hasattr(h, "citation") else {}
            block = (
                f'<chunk doc="{cite.get("title", "")}" '
                f'chunk_id="{h.chunk_id}">\n{h.text}\n</chunk>'
            )
            cost = self.estimator.estimate(block)
            if used + cost > budget:
                break
            blocks.append(block)
            used += cost
        return f"<kb>\n{''.join(blocks)}\n</kb>" if blocks else ""

    async def _build_history(self, budget: int) -> list[dict]:
        session_svc = self.services.session
        if session_svc is None:
            return []

        try:
            window = await session_svc.get_window_for_context(
                self.ctx.session_id, self.ctx.user_id,
            )
        except Exception:
            return []

        msgs = window.messages if hasattr(window, "messages") else []
        result = [self._msg_to_dict(m) for m in msgs[-50:]]

        # Truncate to budget
        total = self.estimator.estimate_messages(result)
        while total > budget and len(result) > 2:
            result = result[2:]  # Drop oldest pair
            total = self.estimator.estimate_messages(result)

        return result

    def _build_current(self, run_messages: list[Message]) -> list[dict]:
        return [self._msg_to_dict(m) for m in run_messages]

    # --- Assembly ---

    def _assemble(
        self,
        role: str, manifest: str, env: str,
        memory: str, kb: str,
        history: list[dict], current: list[dict],
        total_budget: int,
    ) -> tuple[list[dict], dict[str, int], list[str]]:
        sections: dict[str, int] = {}
        dropped: list[str] = []

        sections["role"] = self.estimator.estimate(role)
        sections["manifest"] = self.estimator.estimate(manifest)
        sections["env"] = self.estimator.estimate(env)
        sections["memory"] = self.estimator.estimate(memory) if memory else 0
        sections["kb"] = self.estimator.estimate(kb) if kb else 0
        sections["history"] = self.estimator.estimate_messages(history)
        sections["current"] = self.estimator.estimate_messages(current)

        actual = sum(sections.values())

        # Priority-based truncation: memory → kb → history
        if actual > total_budget and sections["memory"] > 0:
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
            history, dropped_h = self._hard_truncate(
                history, sections["history"] - (actual - total_budget)
            )
            sections["history"] = self.estimator.estimate_messages(history)
            dropped.extend(dropped_h)

        # Build system
        system_parts = [role, manifest, env]
        if memory:
            system_parts.append(memory)
        if kb:
            system_parts.append(kb)

        messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
        messages.extend(history)
        messages.extend(current)

        return messages, sections, dropped

    def _hard_truncate(
        self, history: list[dict], target_tokens: int
    ) -> tuple[list[dict], list[str]]:
        total = self.estimator.estimate_messages(history)
        dropped: list[str] = []
        result = list(history)
        while total > target_tokens and len(result) > 2:
            result = result[2:]
            total = self.estimator.estimate_messages(result)
            dropped.append("history_pair")
        return result, dropped

    # --- Tool rendering ---

    def _render_tools(self) -> list[dict]:
        tool_svc = self.services.tool
        if not tool_svc or not self.spec:
            return []
        tools = tool_svc.list_for_agent(self.spec)
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                },
            }
            for t in tools
        ]

    # --- Helpers ---

    def _derive_query(self) -> str:
        msgs = self.ctx.extra_inputs.get("_run_messages", [])
        for m in reversed(msgs) if msgs else []:
            role = m.role if hasattr(m, "role") else ""
            if role == "user":
                return m.content if hasattr(m, "content") else ""
        return ""

    @staticmethod
    def _msg_to_dict(m: Any) -> dict:
        role = m.role if hasattr(m, "role") else "user"
        content = m.content if hasattr(m, "content") else ""
        result: dict = {"role": role, "content": content}
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.call_id if hasattr(tc, "call_id") else "",
                    "type": "function",
                    "function": {
                        "name": tc.name if hasattr(tc, "name") else "",
                        "arguments": (
                            tc.arguments_raw if hasattr(tc, "arguments_raw") and tc.arguments_raw
                            else str(tc.arguments if hasattr(tc, "arguments") else {})
                        ),
                    },
                }
                for tc in tool_calls
            ]
        tool_call_id = getattr(m, "tool_call_id", "")
        if tool_call_id:
            result["tool_call_id"] = tool_call_id
        name = getattr(m, "name", "")
        if name:
            result["name"] = name
        return result
