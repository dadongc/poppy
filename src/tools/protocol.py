from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from src.common.types import AgentContext


class Tool(Protocol):
    """Agent 工具协议。每个工具声明 name、description、JSON Schema 和 execute。"""

    name: str
    description: str
    schema: dict
    scopes: list[str]
    is_builtin: bool
    cacheable: bool
    cache_ttl: int

    async def execute(
        self,
        ctx: AgentContext,
        args: dict,
    ) -> Any: ...
