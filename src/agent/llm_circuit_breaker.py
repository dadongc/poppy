from __future__ import annotations

from collections import defaultdict, deque

from src.common.clock import now_ts


class CircuitBreaker:
    """滑动窗口熔断器。连续失败 N 次进入 open 状态，cooldown 秒后恢复。"""

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_sec: float = 60.0,
        window_sec: float = 120.0,
    ) -> None:
        self.failures: dict[str, deque[float]] = defaultdict(lambda: deque())
        self.opened_at: dict[str, float] = {}
        self.threshold = failure_threshold
        self.cooldown = cooldown_sec
        self.window = window_sec

    def is_open(self, model: str) -> bool:
        opened = self.opened_at.get(model)
        if opened is None:
            return False
        if now_ts() - opened > self.cooldown:
            self.opened_at.pop(model, None)
            self.failures[model].clear()
            return False
        return True

    def record_failure(self, model: str) -> None:
        q = self.failures[model]
        now = now_ts()
        q.append(now)
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.threshold:
            self.opened_at[model] = now

    def record_success(self, model: str) -> None:
        self.failures[model].clear()
        self.opened_at.pop(model, None)
