from __future__ import annotations

from src.agent.llm_circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_initial_state_closed(self, circuit_breaker):
        assert not circuit_breaker.is_open("gpt-4o")

    def test_opens_after_threshold_failures(self, circuit_breaker):
        for _ in range(3):
            circuit_breaker.record_failure("gpt-4o")
        assert circuit_breaker.is_open("gpt-4o")

    def test_closes_after_cooldown(self, circuit_breaker):
        cb = CircuitBreaker(failure_threshold=2, cooldown_sec=0.01, window_sec=10.0)
        for _ in range(2):
            cb.record_failure("gpt-4o")
        assert cb.is_open("gpt-4o")
        import time
        time.sleep(0.02)
        assert not cb.is_open("gpt-4o")

    def test_success_resets_failures(self, circuit_breaker):
        circuit_breaker.record_failure("gpt-4o")
        circuit_breaker.record_failure("gpt-4o")
        circuit_breaker.record_success("gpt-4o")
        # After success, counter is cleared
        assert not circuit_breaker.is_open("gpt-4o")

    def test_per_model_isolation(self, circuit_breaker):
        for _ in range(3):
            circuit_breaker.record_failure("gpt-4o")
        assert circuit_breaker.is_open("gpt-4o")
        assert not circuit_breaker.is_open("deepseek-chat")

    def test_window_cleanup(self):
        cb = CircuitBreaker(failure_threshold=5, cooldown_sec=60.0, window_sec=0.01)
        cb.record_failure("gpt-4o")
        import time
        time.sleep(0.02)
        # Old failures expired, new one doesn't push over threshold
        assert not cb.is_open("gpt-4o")
