from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from src.agent.base_agent import BaseAgent
from src.common.clock import now_ts
from src.common.ids import EVENT_ID, RUN_ID
from src.common.types import (
    AgentContext,
    AgentSpec,
    Event,
    EventType,
    Message,
)

if TYPE_CHECKING:
    from src.runtime.runtime import Runtime


class Orchestrator:
    """per-Run 编排器。封装 BaseAgent 生命周期、事件发布、SubAgent spawn。"""

    def __init__(self, *, ctx: AgentContext, runtime: Runtime) -> None:
        self._ctx = ctx
        self._runtime = runtime
        self._agent: BaseAgent | None = None
        self._sub_run_ids: list[str] = []

    async def run(self) -> Message:
        ctx = self._ctx
        bus = ctx.services.event_bus
        run_registry = ctx.services.run_registry

        # Inject orchestrator for tools
        ctx.extra_inputs["_orch"] = self

        # Instantiate agent (allow pre-set for testing)
        if self._agent is None:
            self._agent = BaseAgent(ctx=ctx, orchestrator=self)

        # State: running
        if run_registry:
            await run_registry.update_state(ctx.run_id, "running")
        if bus:
            await bus.publish(Event(
                event_id=EVENT_ID(),
                type=EventType.RUN_STARTED,
                run_id=ctx.run_id,
                parent_run_id=ctx.parent_run_id,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                ts=now_ts(),
                payload={"agent_name": ctx.spec.name if ctx.spec else "unknown"},
            ))

        try:
            final = await self._agent.run_loop()
        except asyncio.CancelledError:
            if bus:
                await bus.publish(Event(
                    event_id=EVENT_ID(),
                    type=EventType.RUN_CANCELLED,
                    run_id=ctx.run_id,
                    session_id=ctx.session_id,
                    user_id=ctx.user_id,
                    ts=now_ts(),
                    payload={},
                ))
            if run_registry:
                await run_registry.update_state(ctx.run_id, "cancelled")
            raise

        # Persist messages
        await self._persist_run_messages()

        # Complete
        if run_registry:
            await run_registry.update_state(
                ctx.run_id, "completed",
                used_tokens=ctx.used_tokens,
                used_steps=ctx.used_steps,
            )
        if bus:
            await bus.publish(Event(
                event_id=EVENT_ID(),
                type=EventType.RUN_COMPLETED,
                run_id=ctx.run_id,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                ts=now_ts(),
                payload={
                    "used_tokens": ctx.used_tokens,
                    "used_steps": ctx.used_steps,
                    "final_message": final.content[:500],
                },
            ))
        return final

    # ------------------------------------------------------------------
    # SubAgent spawn
    # ------------------------------------------------------------------

    async def spawn_subagent(
        self,
        *,
        agent_type: str | None = None,
        skills: list[str] | None = None,
        task: str,
        token_budget: int | None = None,
        deadline_sec: int | None = None,
        extra_inputs: dict | None = None,
    ) -> Message:
        """Spawn a sub-agent, either from a pre-registered type or by dynamically
        assembling skills.  Enforces depth / concurrency limits and propagates
        the parent cancel event so that cancellation cascades correctly.
        """
        if not agent_type and not skills:
            raise ValueError("spawn_subagent requires agent_type or skills")

        parent_ctx = self._ctx
        services = parent_ctx.services
        run_registry = services.run_registry

        # --- depth guard ---------------------------------------------------
        max_depth = self._max_sub_agent_depth
        if parent_ctx.depth + 1 > max_depth:
            raise RuntimeError(
                f"max sub-agent depth ({max_depth}) exceeded"
            )

        # --- concurrency guard ---------------------------------------------
        if run_registry:
            max_parallel = self._max_parallel_sub_agents
            active = await run_registry.count_active_children(parent_ctx.run_id)
            if active >= max_parallel:
                raise RuntimeError(
                    f"max parallel sub-agents ({max_parallel}) reached"
                )

        # --- resolve spec --------------------------------------------------
        if agent_type:
            spec = await self._spawn_by_type(agent_type)
        else:
            spec = await self._spawn_by_skills(skills)  # type: ignore[arg-type]

        # --- build sub-context ---------------------------------------------
        effective_deadline = deadline_sec or spec.deadline_sec
        sub_run_id = RUN_ID()

        sub_ctx = AgentContext(
            run_id=sub_run_id,
            parent_run_id=parent_ctx.run_id,
            session_id=parent_ctx.session_id,
            user_id=parent_ctx.user_id,
            trace_id=parent_ctx.trace_id,
            depth=parent_ctx.depth + 1,
            spec=replace(
                spec,
                token_budget=token_budget or spec.token_budget,
                deadline_sec=effective_deadline,
            ),
            user_message=Message(role="user", content=task, created_at=now_ts()),
            extra_inputs=extra_inputs or {},
            cancel_event=parent_ctx.cancel_event,
            deadline_at=now_ts() + effective_deadline,
            started_at=now_ts(),
            services=services,
        )

        if run_registry:
            await run_registry.register(
                sub_run_id,
                agent_name=spec.name,
                session_id=sub_ctx.session_id,
                user_id=sub_ctx.user_id,
                parent_run_id=parent_ctx.run_id,
            )
            await run_registry.attach_cancel_event(sub_run_id, parent_ctx.cancel_event)

        if services.event_bus:
            await services.event_bus.publish(Event(
                event_id=EVENT_ID(),
                type=EventType.SUBAGENT_STARTED,
                run_id=parent_ctx.run_id,
                session_id=parent_ctx.session_id,
                user_id=parent_ctx.user_id,
                ts=now_ts(),
                payload={
                    "sub_run_id": sub_run_id,
                    "agent_name": spec.name,
                    "task": task,
                    "source": spec.source,
                },
            ))

        # --- execute -------------------------------------------------------
        sub_orch = Orchestrator(ctx=sub_ctx, runtime=self._runtime)
        sub_ctx.extra_inputs["_orch"] = sub_orch
        self._sub_run_ids.append(sub_run_id)

        # Apply timeout based on remaining parent deadline
        remaining = parent_ctx.deadline_at - now_ts()
        timeout = min(effective_deadline, remaining) if remaining > 0 else effective_deadline

        try:
            final = await asyncio.wait_for(sub_orch.run(), timeout=timeout)
        except TimeoutError:
            if run_registry:
                await run_registry.update_state(sub_run_id, "timeout")
            return Message(
                role="assistant",
                content="[SubAgent timed out]",
                created_at=now_ts(),
            )
        except Exception:
            return Message(
                role="assistant",
                content="[SubAgent failed]",
                created_at=now_ts(),
            )

        # --- merge token usage back to parent ------------------------------
        parent_ctx.used_tokens += sub_ctx.used_tokens

        if services.event_bus:
            await services.event_bus.publish(Event(
                event_id=EVENT_ID(),
                type=EventType.SUBAGENT_COMPLETED,
                run_id=parent_ctx.run_id,
                session_id=parent_ctx.session_id,
                user_id=parent_ctx.user_id,
                ts=now_ts(),
                payload={
                    "sub_run_id": sub_run_id,
                    "result": final.content[:500],
                    "used_tokens": sub_ctx.used_tokens,
                },
            ))
        return final

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _spawn_by_type(self, agent_type: str) -> AgentSpec:
        """Resolve a pre-registered agent and intersect its permissions with
        the parent's allowed tools / skills."""
        parent_spec = self._ctx.spec
        services = self._ctx.services

        agent_registry = services.agent_registry
        if agent_registry is None:
            raise RuntimeError("agent_registry not available")

        spec = await agent_registry.resolve(agent_type)
        if spec is None:
            raise RuntimeError(f"subagent not found: {agent_type}")

        # Intersect with parent's allowed sets
        if parent_spec:
            spec = replace(
                spec,
                allowed_tools=spec.allowed_tools & parent_spec.allowed_tools,
                allowed_skills=spec.allowed_skills & parent_spec.allowed_skills,
            )
        return spec

    async def _spawn_by_skills(self, skill_names: list[str]) -> AgentSpec:
        """Dynamically assemble an ephemeral AgentSpec from one or more skill
        definitions.  The resulting spec merges required_tools, instructions,
        and agent_profile from every named skill."""
        parent_spec = self._ctx.spec
        services = self._ctx.services

        skill_registry = services.skill
        if skill_registry is None:
            raise RuntimeError("skill_registry not available")

        merged_tools: set[str] = set()
        merged_skills: set[str] = set()
        instructions: list[str] = []
        descriptions: list[str] = []
        suffix_parts: list[str] = []
        max_steps = 0
        total_token_budget = 0
        max_deadline = 0
        preferred_model = ""
        temperature = 0.7

        for name in skill_names:
            sk = await skill_registry.get(name)
            if sk is None:
                raise RuntimeError(f"skill not found: {name}")
            merged_tools.update(sk.required_tools)
            descriptions.append(sk.display_name or sk.description or name)
            instructions.append(sk.instructions)
            max_steps = max(max_steps, sk.agent_profile.max_steps)
            total_token_budget += sk.agent_profile.token_budget
            max_deadline = max(max_deadline, sk.agent_profile.deadline_sec)
            if sk.agent_profile.system_prompt_suffix:
                suffix_parts.append(sk.agent_profile.system_prompt_suffix)
            if sk.agent_profile.preferred_model and not preferred_model:
                preferred_model = sk.agent_profile.preferred_model
            temperature = sk.agent_profile.temperature  # last wins

        agent_name = "+".join(skill_names)
        system_prompt = "\n\n".join(
            f"## {d}\n{inst}" for d, inst in zip(descriptions, instructions, strict=True)
        )
        if suffix_parts:
            system_prompt += "\n\n" + "\n\n".join(suffix_parts)

        # Intersect with parent's allowed sets
        if parent_spec:
            merged_tools &= parent_spec.allowed_tools
            merged_skills &= parent_spec.allowed_skills

        return AgentSpec(
            name=agent_name,
            description=", ".join(descriptions),
            system_prompt=system_prompt,
            preferred_model=preferred_model,
            temperature=temperature,
            allowed_tools=merged_tools,
            allowed_skills=merged_skills,
            max_steps=max_steps,
            token_budget=total_token_budget,
            deadline_sec=max_deadline,
            source="ephemeral",
        )

    @property
    def _max_sub_agent_depth(self) -> int:
        if self._ctx.spec and self._ctx.spec.max_sub_agent_depth > 0:
            return self._ctx.spec.max_sub_agent_depth
        return self._runtime._config.agent.max_sub_agent_depth

    @property
    def _max_parallel_sub_agents(self) -> int:
        if self._ctx.spec and self._ctx.spec.max_parallel_sub_agents > 0:
            return self._ctx.spec.max_parallel_sub_agents
        return self._runtime._config.agent.max_parallel_sub_agents

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist_run_messages(self) -> None:
        ctx = self._ctx
        if self._agent and self._agent.run_messages:
            session_svc = ctx.services.session
            if session_svc:
                for msg in self._agent.run_messages:
                    try:
                        await session_svc.append_message(
                            ctx.session_id, ctx.user_id, msg,
                        )
                    except Exception:
                        pass
