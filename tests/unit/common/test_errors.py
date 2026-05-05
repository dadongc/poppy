from __future__ import annotations

from src.common.errors import (
    AgentError,
    BudgetExceededError,
    CancelledError,
    ConfigError,
    InfraError,
    LLMProviderError,
    NotFoundError,
    PermissionDeniedError,
    TimeoutError,
    ToolError,
)


class TestErrorHierarchy:
    def test_all_inherit_from_agent_error(self):
        assert issubclass(InfraError, AgentError)
        assert issubclass(ConfigError, AgentError)
        assert issubclass(PermissionDeniedError, AgentError)
        assert issubclass(NotFoundError, AgentError)
        assert issubclass(BudgetExceededError, AgentError)
        assert issubclass(CancelledError, AgentError)
        assert issubclass(TimeoutError, AgentError)
        assert issubclass(LLMProviderError, AgentError)
        assert issubclass(ToolError, AgentError)

    def test_agent_error_is_exception(self):
        assert issubclass(AgentError, Exception)


class TestInfraError:
    def test_message(self):
        e = InfraError("db down")
        assert str(e) == "db down"


class TestConfigError:
    def test_message(self):
        e = ConfigError("missing key")
        assert str(e) == "missing key"


class TestPermissionDeniedError:
    def test_message(self):
        e = PermissionDeniedError("no access")
        assert str(e) == "no access"


class TestNotFoundError:
    def test_message(self):
        e = NotFoundError("session not found")
        assert str(e) == "session not found"


class TestBudgetExceededError:
    def test_message(self):
        e = BudgetExceededError("token budget exceeded")
        assert str(e) == "token budget exceeded"


class TestCancelledError:
    def test_message(self):
        e = CancelledError("run cancelled")
        assert str(e) == "run cancelled"


class TestTimeoutError:
    def test_message(self):
        e = TimeoutError("deadline reached")
        assert str(e) == "deadline reached"


class TestLLMProviderError:
    def test_with_error(self):
        from src.common.types import LLMError as LLMErrorType

        inner = LLMErrorType(type="rate_limit", message="too many requests")
        e = LLMProviderError("provider error", error=inner)
        assert e.error.type == "rate_limit"
        assert e.error.retryable is False

    def test_retryable(self):
        from src.common.types import LLMError as LLMErrorType

        inner = LLMErrorType(type="network", message="timeout", retryable=True)
        e = LLMProviderError("network error", error=inner)
        assert e.error.retryable is True


class TestToolError:
    def test_defaults(self):
        e = ToolError("tool failed")
        assert e.tool_name == ""
        assert e.error_type == "unknown"

    def test_with_details(self):
        e = ToolError("search failed", tool_name="search", error_type="timeout")
        assert e.tool_name == "search"
        assert e.error_type == "timeout"
