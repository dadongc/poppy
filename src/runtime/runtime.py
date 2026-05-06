from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from src.agent.llm_circuit_breaker import CircuitBreaker
from src.agent.llm_gateway import LLMGateway
from src.agent.llm_providers.openai import OpenAiProvider
from src.agent.llm_router import ModelRouter
from src.agent.orchestrator import Orchestrator
from src.common.clock import now_ts
from src.common.config import AppConfig, load_config
from src.common.errors import NotFoundError
from src.common.ids import RUN_ID
from src.common.types import AgentContext, Message, Services
from src.infra.factory import Infra, build_infra
from src.runtime.agent_registry import AgentRegistry
from src.runtime.run_registry import RunRegistry
from src.service.container import build_services
from src.skills.registry import SkillRegistry
from src.tools.registry import ToolRegistry

if TYPE_CHECKING:
    pass


class Runtime:
    """进程级单例容器。整个应用只有一个实例。"""

    _instance: Runtime | None = None

    def __init__(
        self,
        *,
        infra: Infra,
        services: Services,
        llm: LLMGateway,
        agent_registry: AgentRegistry,
        tool_registry: ToolRegistry,
        run_registry: RunRegistry,
        config: AppConfig,
    ) -> None:
        self._infra = infra
        self._services = services
        self._llm = llm
        self._agents = agent_registry
        self._tools = tool_registry
        self._runs = run_registry
        self._bus = infra.eventbus
        self._config = config
        self._workers: list[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()

    @classmethod
    async def initialize(cls, config_path: str = "config/dev.yaml") -> Runtime:
        cfg = load_config(config_path)

        # Build infra
        infra = await build_infra(cfg.infra.model_dump(), run_migrations_flag=False)

        # Build LLM Gateway
        llm = _build_llm_gateway(cfg)

        # Build services
        services = await build_services(infra, cfg=cfg)

        # Agent registry
        agent_registry = AgentRegistry(path=cfg.agent.registry_path)
        await agent_registry.load()

        # Tool registry
        tool_registry = ToolRegistry()
        await tool_registry.load_builtins()

        # Skill registry
        skill_registry = SkillRegistry(skills_path=cfg.agent.skills_path)
        await skill_registry.load()

        # Run registry
        run_registry = RunRegistry(store=infra.relational)
        await run_registry.init()

        # Wire services
        services.tool = tool_registry
        services.llm = llm
        services.event_bus = infra.eventbus
        services.run_registry = run_registry
        services.agent_registry = agent_registry
        services.skill = skill_registry

        rt = cls(
            infra=infra,
            services=services,
            llm=llm,
            agent_registry=agent_registry,
            tool_registry=tool_registry,
            run_registry=run_registry,
            config=cfg,
        )
        cls._instance = rt
        return rt

    @classmethod
    def current(cls) -> Runtime:
        if cls._instance is None:
            raise RuntimeError("Runtime not initialized")
        return cls._instance

    async def shutdown(self, timeout: float = 30.0) -> None:
        self._shutdown_event.set()

        active_runs = await self._runs.list_active()
        for r in active_runs:
            await self._runs.cancel(r.run_id)

        try:
            await asyncio.wait(self._workers, timeout=timeout)
        except TimeoutError:
            pass

        for w in self._workers:
            w.cancel()

        await self._bus.shutdown()
        await self._infra.relational.close()

    async def start_run(
        self,
        *,
        agent_name: str,
        user_id: str,
        session_id: str = "",
        user_message: str,
        extra_inputs: dict | None = None,
    ) -> str:
        spec = await self._agents.resolve(agent_name)
        if not spec:
            raise NotFoundError(f"agent not found: {agent_name}")

        run_id = RUN_ID()
        ctx = AgentContext(
            run_id=run_id,
            parent_run_id=None,
            session_id=session_id,
            user_id=user_id,
            trace_id=run_id,
            spec=spec,
            user_message=Message(role="user", content=user_message, created_at=now_ts()),
            extra_inputs=extra_inputs or {},
            cancel_event=asyncio.Event(),
            deadline_at=now_ts() + spec.deadline_sec,
            started_at=now_ts(),
            services=self._services,
        )

        await self._runs.register(
            run_id,
            agent_name=spec.name,
            session_id=session_id,
            user_id=user_id,
            parent_run_id=None,
        )
        await self._runs.attach_cancel_event(run_id, ctx.cancel_event)

        orch = Orchestrator(ctx=ctx, runtime=self)
        asyncio.create_task(self._run_with_lifecycle(orch, ctx))
        return run_id

    async def _run_with_lifecycle(self, orch: Orchestrator, ctx: AgentContext) -> None:
        try:
            await orch.run()
        except asyncio.CancelledError:
            await self._runs.update_state(ctx.run_id, "cancelled")
        except Exception:
            await self._runs.update_state(ctx.run_id, "failed")

    async def wait_run(self, run_id: str, timeout: float = 300.0) -> None:
        """Wait for a run to reach terminal state."""
        deadline = now_ts() + timeout
        while now_ts() < deadline:
            info = await self._runs.get(run_id)
            if info is None or info.state in ("completed", "failed", "cancelled", "timeout"):
                return
            await asyncio.sleep(1.0)

    @property
    def services(self) -> Services:
        return self._services

    @property
    def event_bus(self):
        return self._bus


def _build_llm_gateway(cfg: AppConfig) -> LLMGateway:
    from pathlib import Path

    providers: dict = {}
    for name, pcfg in cfg.llm.providers.items():
        api_key = pcfg.get("api_key", os.environ.get(f"{name.upper()}_API_KEY", ""))
        base_url = pcfg.get("base_url", "")
        providers[name] = OpenAiProvider(
            api_key=api_key,
            base_url=base_url,
        )

    catalog_path = Path(__file__).parent.parent / "agent" / "llm_catalog.yaml"
    router = ModelRouter(str(catalog_path), providers)

    cb = CircuitBreaker()
    return LLMGateway(router=router, circuit_breaker=cb)
