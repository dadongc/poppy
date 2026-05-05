from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.common.types import LLMError  # noqa: F401


class AgentError(Exception):
    """所有自定义异常的根。"""


class InfraError(AgentError):
    """基础设施层异常（DB/缓存/存储）。不可恢复。"""


class ConfigError(AgentError):
    """配置错误。启动时抛。"""


class PermissionDeniedError(AgentError):
    """权限不足。"""


class NotFoundError(AgentError):
    """资源不存在。"""


class BudgetExceededError(AgentError):
    """token / 时间 / 步数预算耗尽。"""


class CancelledError(AgentError):
    """运行被显式取消。"""


class TimeoutError(AgentError):
    """运行超时。"""


class LLMProviderError(AgentError):
    """LLM provider 调用失败。"""

    def __init__(self, msg: str, error: LLMError) -> None:  # noqa: F821
        super().__init__(msg)
        self.error = error


class ToolError(AgentError):
    """工具执行失败。"""

    def __init__(self, msg: str, *, tool_name: str = "", error_type: str = "unknown") -> None:
        super().__init__(msg)
        self.tool_name = tool_name
        self.error_type = error_type
