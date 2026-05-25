from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest

from src.common.config import SchedulerConfig
from src.runtime.scheduler import DailyDigestScheduler


class FakeRuntime:
    """Stub Runtime，记录调度触发次数。"""

    def __init__(self) -> None:
        self.trigger_count = 0
        self.last_agent: str = ""
        self.last_message: str = ""

    async def start_run(self, *, agent_name: str, user_id: str, user_message: str) -> str:
        self.trigger_count += 1
        self.last_agent = agent_name
        self.last_message = user_message
        return "fake-run-id"


@pytest.fixture
def fake_runtime() -> FakeRuntime:
    return FakeRuntime()


@pytest.fixture
def enabled_config() -> SchedulerConfig:
    return SchedulerConfig(
        enabled=True,
        cron="0 8 * * *",
        agent="daily-digest",
        message="test message",
        user_id="test-user",
        timezone="Asia/Shanghai",
    )


@pytest.fixture
def disabled_config() -> SchedulerConfig:
    return SchedulerConfig(
        enabled=False,
        cron="0 8 * * *",
        agent="daily-digest",
        message="test message",
        user_id="test-user",
        timezone="Asia/Shanghai",
    )


class TestSchedulerConfig:
    def test_config_defaults(self) -> None:
        cfg = SchedulerConfig()
        assert cfg.enabled is True
        assert cfg.cron == "0 8 * * *"
        assert cfg.agent == "daily-digest"
        assert cfg.timezone == "Asia/Shanghai"

    def test_config_disabled(self) -> None:
        cfg = SchedulerConfig(enabled=False)
        assert cfg.enabled is False


class TestSchedulerCron:
    def test_cron_parse(self, enabled_config: SchedulerConfig) -> None:
        s = DailyDigestScheduler(runtime=FakeRuntime(), config=enabled_config)
        now = datetime(2026, 5, 25, 7, 0, tzinfo=dt_timezone.utc)
        next_run = s._next_trigger(now)
        # 下一个触发应该是今天 8:00
        assert next_run > now

    def test_next_trigger(self, enabled_config: SchedulerConfig) -> None:
        s = DailyDigestScheduler(runtime=FakeRuntime(), config=enabled_config)
        # 7:00 时，prev 应该是昨天 8:00，next 是今天 8:00
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Shanghai")
        now = datetime(2026, 5, 25, 7, 0, tzinfo=tz)
        prev = s._prev_trigger(now)
        next_run = s._next_trigger(now)
        assert prev.hour == 8
        assert prev.day == 24  # 昨天
        assert next_run.hour == 8
        assert next_run.day == 25  # 今天

    def test_prev_trigger_same_day(self, enabled_config: SchedulerConfig) -> None:
        """在触发时间之后，prev 应该是今天的触发时间。"""
        from zoneinfo import ZoneInfo

        s = DailyDigestScheduler(runtime=FakeRuntime(), config=enabled_config)
        tz = ZoneInfo("Asia/Shanghai")
        now = datetime(2026, 5, 25, 9, 0, tzinfo=tz)
        prev = s._prev_trigger(now)
        assert prev.hour == 8
        assert prev.day == 25  # 今天


class TestSchedulerLifecycle:
    @pytest.mark.asyncio
    async def test_enabled_scheduler_starts(self, enabled_config: SchedulerConfig) -> None:
        rt = FakeRuntime()
        s = DailyDigestScheduler(runtime=rt, config=enabled_config)
        s.start()
        assert s._task is not None
        await asyncio.sleep(0.1)  # 让 loop 跑一下
        await s.stop()

    @pytest.mark.asyncio
    async def test_disabled_scheduler_skips(self, disabled_config: SchedulerConfig) -> None:
        rt = FakeRuntime()
        s = DailyDigestScheduler(runtime=rt, config=disabled_config)
        s.start()
        assert s._task is None  # disabled 时不创建 task

    @pytest.mark.asyncio
    async def test_trigger_now(self, enabled_config: SchedulerConfig) -> None:
        rt = FakeRuntime()
        s = DailyDigestScheduler(runtime=rt, config=enabled_config)
        await s.trigger_now()
        assert rt.trigger_count == 1
        assert rt.last_agent == "daily-digest"

    @pytest.mark.asyncio
    async def test_catch_up_on_start(self, enabled_config: SchedulerConfig) -> None:
        """启动时如果错过了今天的触发时间，自动追补。"""
        rt = FakeRuntime()
        s = DailyDigestScheduler(runtime=rt, config=enabled_config)
        # Mock _now to return a time after today's trigger
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Shanghai")
        s._now = lambda: datetime(2026, 5, 25, 9, 0, tzinfo=tz)  # type: ignore[assignment]
        s.start()
        await asyncio.sleep(0.2)
        await s.stop()
        # 应该有一次追补触发
        assert rt.trigger_count >= 1

    @pytest.mark.asyncio
    async def test_no_catch_up_before_trigger(self, enabled_config: SchedulerConfig) -> None:
        """启动时还没到触发时间，不追补。"""
        rt = FakeRuntime()
        s = DailyDigestScheduler(runtime=rt, config=enabled_config)
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Shanghai")
        s._now = lambda: datetime(2026, 5, 25, 7, 0, tzinfo=tz)  # type: ignore[assignment]
        s.start()
        # 不应该立即触发（还要等到 8:00）
        await asyncio.sleep(0.1)
        await s.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, enabled_config: SchedulerConfig) -> None:
        rt = FakeRuntime()
        s = DailyDigestScheduler(runtime=rt, config=enabled_config)
        s.start()
        assert s._task is not None
        await s.stop()
        assert s._task.done()
