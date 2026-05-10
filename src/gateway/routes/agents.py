from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.gateway.deps import auth_user_id, get_runtime
from src.gateway.schemas import AgentItem
from src.runtime.runtime import Runtime

router = APIRouter()


@router.get("", response_model=list[AgentItem])
async def list_agents(
    user_id: str = Depends(auth_user_id),
    runtime: Runtime = Depends(get_runtime),
) -> list[AgentItem]:
    reg = runtime.services.agent_registry
    if reg is None:
        raise HTTPException(500, "agent registry not available")
    specs = await reg.list()
    return [
        AgentItem(
            name=s.name,
            description=s.description,
            source=s.source,
        )
        for s in specs
    ]
