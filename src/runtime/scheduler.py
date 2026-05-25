from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from croniter import croniter

from src.common.config import SchedulerConfig

logger = logging.getLogger("scheduler")


class DailyDigestScheduler:
    """定时调度器：每天按 cron 表达式触发日报生成。

    启动时自动追补：如果今天的触发时间已过，立即补触发一次。
    """

    def __init__(self, *, runtime, config: SchedulerConfig) -> None:
        self._runtime = runtime
        self._cfg = config
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def _now(self) -> datetime:
        """当前时间，带时区。"""
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo(self._cfg.timezone))
        except Exception:
            return datetime.now(UTC).astimezone()

    def _prev_trigger(self, now: datetime) -> datetime:
        """返回 now 之前最近一次触发时间。"""
        c = croniter(self._cfg.cron, now, ret_type=datetime)
        return c.get_prev(datetime)

    def _next_trigger(self, now: datetime) -> datetime:
        """返回 now 之后下一次触发时间。"""
        c = croniter(self._cfg.cron, now, ret_type=datetime)
        return c.get_next(datetime)

    async def _trigger(self) -> None:
        """执行一次日报生成。"""
        logger.info(
            "Scheduler triggering agent=%s message=%s",
            self._cfg.agent,
            self._cfg.message,
        )
        try:
            await self._runtime.start_run(
                agent_name=self._cfg.agent,
                user_id=self._cfg.user_id,
                user_message=self._cfg.message,
            )
            logger.info("Scheduler trigger done")
        except Exception:
            logger.exception("Scheduler trigger failed")

    async def _loop(self) -> None:
        """主循环：追补 → 等下次 → 触发 → 重复。"""
        now = self._now()
        prev = self._prev_trigger(now)
        next_run = self._next_trigger(now)

        # 追补：如果上次触发时间在今天且还没到下一次触发时间，说明今天还没执行过
        if prev.date() == now.date() and now > prev:
            logger.info("Scheduler catch-up: triggering for missed run at %s", prev)
            await self._trigger()
            # 重新计算下一次
            now = self._now()
            next_run = self._next_trigger(now)

        logger.info("Scheduler next run: %s", next_run.isoformat())

        while not self._stop.is_set():
            now = self._now()
            if now >= next_run:
                await self._trigger()
                now = self._now()
                next_run = self._next_trigger(now)
                logger.info("Scheduler next run: %s", next_run.isoformat())

            wait_sec = (next_run - self._now()).total_seconds()
            if wait_sec > 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=min(wait_sec, 60))
                except TimeoutError:
                    pass

    def start(self) -> None:
        """启动后台调度任务。"""
        if not self._cfg.enabled:
            logger.info("Scheduler disabled, skipping")
            return
        self._task = asyncio.ensure_future(self._loop())
        logger.info("Scheduler started, cron=%s", self._cfg.cron)

    async def stop(self) -> None:
        """停止调度器。"""
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Scheduler stopped")

    async def trigger_now(self) -> None:
        """手动立即触发一次（调试用）。"""
        logger.info("Scheduler manual trigger")
        await self._trigger()
