from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from src.agent.orchestrator import Orchestrator
from src.common.clock import now_ts
from src.common.ids import new_id
from src.common.types import AgentContext, AgentSpec, Event, EventType, LLMChunk, Message, Services
from src.skills.registry import Skill, SkillProfile


class StubLLMGateway:
    """Minimal LLM gateway stub."""

    async def stream(self, payload, ctx):
        yield LLMChunk(type="text_delta", text="Hi")
        yield LLMChunk(type="stop", stop_reason="end")


class FakeBaseAgent:
    """Fake BaseAgent that immediately returns a final message."""

    def __init__(self, ctx, orchestrator):
        self.ctx = ctx
        self.orch = orchestrator
        self.run_messages = []

    async def run_loop(self):
        msg = Message(role="assistant", content="done", created_at=now_ts())
        self.run_messages.append(msg)
        return msg


class FakeBus:
    def __init__(self):
        self.events: list[Event] = []

    async def publish(self, event):
        self.events.append(event)

    async def init(self):
        pass

    async def shutdown(self):
        pass


class FakeRunRegistry:
    def __init__(self):
        self.states: dict[str, str] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self._active_children = 0

    async def register(self, *args, **kwargs):
        pass

    async def update_state(self, run_id, state, **fields):
        self.states[run_id] = state

    async def attach_cancel_event(self, run_id, ev):
        self.cancel_events[run_id] = ev

    async def count_active_children(self, run_id) -> int:
        return self._active_children


class FakeAgentRegistry:
    async def resolve(self, name):
        return AgentSpec(
            name=name,
            allowed_tools={"final_answer"},
            max_steps=5,
            token_budget=10000,
            deadline_sec=60,
        )


class FakeSkillRegistry:
    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def add(self, sk: Skill) -> None:
        self._skills[sk.name] = sk

    async def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    async def list(self) -> list[Skill]:
        return list(self._skills.values())


@dataclass
class FakeAgentConfig:
    max_sub_agent_depth: int = 3
    max_parallel_sub_agents: int = 5


@dataclass
class FakeAppConfig:
    agent: FakeAgentConfig = field(default_factory=FakeAgentConfig)


class FakeRuntime:
    def __init__(self, agent: FakeAgentConfig | None = None):
        self._config = FakeAppConfig(agent=agent or FakeAgentConfig())


class TestOrchestrator:
    @pytest.mark.asyncio
    async def test_run_publishes_events(self):
        bus = FakeBus()
        run_registry = FakeRunRegistry()
        services = Services(
            event_bus=bus, run_registry=run_registry, llm=StubLLMGateway(),
        )

        ctx = AgentContext(
            run_id=new_id("run"),
            session_id=new_id("ses"),
            user_id="u1",
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
            services=services,
        )

        orch = Orchestrator(ctx=ctx, runtime=FakeRuntime())
        # Replace agent creation
        orch._agent = FakeBaseAgent(ctx, orch)

        result = await orch.run()
        assert result.content == "done"

        event_types = [e.type for e in bus.events]
        assert EventType.RUN_STARTED in event_types
        assert EventType.RUN_COMPLETED in event_types
        assert run_registry.states.get(ctx.run_id) == "completed"

    @pytest.mark.asyncio
    async def test_cancel_publishes_cancelled_event(self):
        bus = FakeBus()
        run_registry = FakeRunRegistry()
        services = Services(
            event_bus=bus, run_registry=run_registry, llm=StubLLMGateway(),
        )

        ctx = AgentContext(
            run_id=new_id("run"),
            session_id=new_id("ses"),
            user_id="u1",
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
            services=services,
        )

        orch = Orchestrator(ctx=ctx, runtime=FakeRuntime())

        class CancellingAgent(FakeBaseAgent):
            async def run_loop(self):
                raise asyncio.CancelledError()

        orch._agent = CancellingAgent(ctx, orch)

        result = await orch.run()
        assert result.content == "[Run cancelled]"

        event_types = [e.type for e in bus.events]
        assert EventType.RUN_CANCELLED in event_types
        assert run_registry.states.get(ctx.run_id) == "cancelled"

    @pytest.mark.asyncio
    async def test_spawn_subagent(self):
        bus = FakeBus()
        run_registry = FakeRunRegistry()
        agent_registry = FakeAgentRegistry()
        services = Services(
            event_bus=bus,
            run_registry=run_registry,
            agent_registry=agent_registry,
            llm=StubLLMGateway(),
        )

        ctx = AgentContext(
            run_id=new_id("run"),
            session_id=new_id("ses"),
            user_id="u1",
            trace_id=new_id("trc"),
            spec=AgentSpec(
                name="parent",
                allowed_tools={"final_answer", "delegate_task"},
                max_steps=10,
                token_budget=50000,
                deadline_sec=300,
            ),
            user_message=Message(role="user", content="hi", created_at=now_ts()),
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + 300,
            started_at=now_ts(),
            services=services,
        )

        orch = Orchestrator(ctx=ctx, runtime=FakeRuntime())
        orch._agent = FakeBaseAgent(ctx, orch)

        result = await orch.spawn_subagent(
            agent_type="sub",
            task="do something",
        )
        assert result.role == "assistant"
        # Subagent uses StubLLMGateway which returns "Hi" (no tool calls → final answer)
        assert result.content == "Hi"

        sub_events = [e for e in bus.events if e.type == EventType.SUBAGENT_STARTED]
        assert len(sub_events) >= 1

    @pytest.mark.asyncio
    async def test_spawn_subagent_depth_limit(self):
        """spawn_subagent raises when max depth is exceeded."""
        bus = FakeBus()
        run_registry = FakeRunRegistry()
        services = Services(
            event_bus=bus,
            run_registry=run_registry,
            agent_registry=FakeAgentRegistry(),
            llm=StubLLMGateway(),
        )

        ctx = AgentContext(
            run_id=new_id("run"),
            session_id=new_id("ses"),
            user_id="u1",
            trace_id=new_id("trc"),
            depth=3,  # already at max
            spec=AgentSpec(
                name="parent",
                max_sub_agent_depth=3,
                allowed_tools={"final_answer", "delegate_task"},
            ),
            user_message=Message(role="user", content="hi", created_at=now_ts()),
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + 300,
            started_at=now_ts(),
            services=services,
        )

        fake_rt = FakeRuntime(agent=FakeAgentConfig(max_sub_agent_depth=3))
        orch = Orchestrator(ctx=ctx, runtime=fake_rt)
        orch._agent = FakeBaseAgent(ctx, orch)

        with pytest.raises(RuntimeError, match="max sub-agent depth"):
            await orch.spawn_subagent(agent_type="sub", task="do something")

    @pytest.mark.asyncio
    async def test_spawn_subagent_concurrency_limit(self):
        """spawn_subagent raises when max parallel sub-agents is reached."""
        run_registry = FakeRunRegistry()
        run_registry._active_children = 5  # at max
        services = Services(
            event_bus=FakeBus(),
            run_registry=run_registry,
            agent_registry=FakeAgentRegistry(),
            llm=StubLLMGateway(),
        )

        ctx = AgentContext(
            run_id=new_id("run"),
            session_id=new_id("ses"),
            user_id="u1",
            trace_id=new_id("trc"),
            spec=AgentSpec(
                name="parent",
                max_parallel_sub_agents=5,
                allowed_tools={"final_answer", "delegate_task"},
            ),
            user_message=Message(role="user", content="hi", created_at=now_ts()),
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + 300,
            started_at=now_ts(),
            services=services,
        )

        fake_rt = FakeRuntime(agent=FakeAgentConfig(max_parallel_sub_agents=5))
        orch = Orchestrator(ctx=ctx, runtime=fake_rt)
        orch._agent = FakeBaseAgent(ctx, orch)

        with pytest.raises(RuntimeError, match="max parallel sub-agents"):
            await orch.spawn_subagent(agent_type="sub", task="do something")

    @pytest.mark.asyncio
    async def test_spawn_subagent_by_skills(self):
        """spawn_subagent with skills dynamically assembles ephemeral AgentSpec."""
        skill_registry = FakeSkillRegistry()
        skill_registry.add(Skill(
            name="researcher",
            description="调研分析",
            required_tools=["read_artifact", "remember"],
            instructions="你是调研分析 Agent。",
            agent_profile=SkillProfile(max_steps=5, token_budget=15000, deadline_sec=90),
        ))
        skill_registry.add(Skill(
            name="coder",
            description="代码编写",
            required_tools=["read_artifact", "delegate_task"],
            instructions="你是代码编写 Agent。",
            agent_profile=SkillProfile(max_steps=8, token_budget=20000, deadline_sec=120),
        ))

        bus = FakeBus()
        run_registry = FakeRunRegistry()
        services = Services(
            event_bus=bus,
            run_registry=run_registry,
            skill=skill_registry,
            llm=StubLLMGateway(),
        )

        ctx = AgentContext(
            run_id=new_id("run"),
            session_id=new_id("ses"),
            user_id="u1",
            trace_id=new_id("trc"),
            spec=AgentSpec(
                name="parent",
                allowed_tools={"read_artifact", "remember", "delegate_task", "final_answer"},
                max_steps=10,
                token_budget=50000,
                deadline_sec=300,
            ),
            user_message=Message(role="user", content="hi", created_at=now_ts()),
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + 300,
            started_at=now_ts(),
            services=services,
        )

        orch = Orchestrator(ctx=ctx, runtime=FakeRuntime())
        orch._agent = FakeBaseAgent(ctx, orch)

        result = await orch.spawn_subagent(
            skills=["researcher", "coder"],
            task="research and write",
        )
        assert result.role == "assistant"
        assert result.content == "Hi"

        sub_events = [e for e in bus.events if e.type == EventType.SUBAGENT_STARTED]
        assert len(sub_events) == 1
        assert sub_events[0].payload["agent_name"] == "researcher+coder"
        assert sub_events[0].payload["source"] == "ephemeral"

    @pytest.mark.asyncio
    async def test_spawn_subagent_shares_cancel_event(self):
        """Child cancel_event is the same as parent's, and attach_cancel_event is called."""
        run_registry = FakeRunRegistry()
        services = Services(
            event_bus=FakeBus(),
            run_registry=run_registry,
            agent_registry=FakeAgentRegistry(),
            llm=StubLLMGateway(),
        )

        parent_cancel_ev = asyncio.Event()
        ctx = AgentContext(
            run_id=new_id("run"),
            session_id=new_id("ses"),
            user_id="u1",
            trace_id=new_id("trc"),
            spec=AgentSpec(
                name="parent",
                allowed_tools={"final_answer", "delegate_task"},
                max_steps=10,
                token_budget=50000,
                deadline_sec=300,
            ),
            user_message=Message(role="user", content="hi", created_at=now_ts()),
            cancel_event=parent_cancel_ev,
            deadline_at=now_ts() + 300,
            started_at=now_ts(),
            services=services,
        )

        orch = Orchestrator(ctx=ctx, runtime=FakeRuntime())
        orch._agent = FakeBaseAgent(ctx, orch)

        await orch.spawn_subagent(agent_type="sub", task="do something")

        # The cancel event should have been attached to the sub run_id
        assert len(run_registry.cancel_events) == 1
        attached_ev = list(run_registry.cancel_events.values())[0]
        assert attached_ev is parent_cancel_ev

    @pytest.mark.asyncio
    async def test_spawn_subagent_no_agent_type_or_skills(self):
        """spawn_subagent without agent_type or skills raises ValueError."""
        ctx = AgentContext(
            run_id=new_id("run"),
            session_id=new_id("ses"),
            user_id="u1",
            trace_id=new_id("trc"),
            spec=AgentSpec(name="parent"),
            user_message=Message(role="user", content="hi", created_at=now_ts()),
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + 300,
            started_at=now_ts(),
            services=Services(),
        )

        orch = Orchestrator(ctx=ctx, runtime=FakeRuntime())
        orch._agent = FakeBaseAgent(ctx, orch)

        with pytest.raises(ValueError, match="requires agent_type or skills"):
            await orch.spawn_subagent(task="do something")
