from __future__ import annotations

from collections.abc import AsyncIterator

from src.common.clock import now_ts
from src.common.ids import JOB_ID
from src.infra.protocols import Job

NOTIFY_CHANNEL = "agent_jobs"


class PgJobQueue:
    """PostgreSQL-backed job queue with FOR UPDATE SKIP LOCKED.

    Uses LISTEN/NOTIFY to wake workers when new jobs are enqueued.
    """

    def __init__(self, store) -> None:
        self._store = store

    async def init(self) -> None:
        pass

    async def enqueue(
        self,
        job_type: str,
        payload: dict,
        *,
        priority: int = 0,
        scheduled_at: float | None = None,
        max_retries: int = 3,
    ) -> str:
        job_id = JOB_ID()
        scheduled_ts = scheduled_at or now_ts()
        await self._store.execute(
            """INSERT INTO async_jobs(job_id, job_type, payload, priority,
               scheduled_at, max_retries)
               VALUES ($1, $2, $3, $4, to_timestamp($5), $6)""",
            job_id,
            job_type,
            payload,
            priority,
            scheduled_ts,
            max_retries,
        )
        await self._store.notify(NOTIFY_CHANNEL, job_type)
        return job_id

    async def claim_next(
        self,
        worker_id: str,
        job_types: list[str] | None = None,
        lease_sec: int = 300,
    ) -> Job | None:
        if job_types:
            rows = await self._store.fetch_all(
                """SELECT * FROM async_jobs
                   WHERE state = 'pending'
                     AND scheduled_at <= NOW()
                     AND job_type = ANY($1)
                   ORDER BY priority DESC, scheduled_at ASC
                   LIMIT 1
                   FOR UPDATE SKIP LOCKED""",
                job_types,
            )
        else:
            rows = await self._store.fetch_all(
                """SELECT * FROM async_jobs
                   WHERE state = 'pending'
                     AND scheduled_at <= NOW()
                   ORDER BY priority DESC, scheduled_at ASC
                   LIMIT 1
                   FOR UPDATE SKIP LOCKED"""
            )

        if not rows:
            return None

        row = rows[0]
        await self._store.execute(
            """UPDATE async_jobs
               SET state = 'running',
                   locked_by = $1,
                   started_at = NOW(),
                   locked_until = NOW() + ($2 || ' seconds')::INTERVAL
               WHERE job_id = $3""",
            worker_id,
            str(lease_sec),
            row["job_id"],
        )
        return Job(
            job_id=row["job_id"],
            job_type=row["job_type"],
            payload=row["payload"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            locked_until=now_ts() + lease_sec,
        )

    async def mark_done(self, job_id: str) -> None:
        await self._store.execute(
            """UPDATE async_jobs
               SET state = 'done', finished_at = NOW()
               WHERE job_id = $1""",
            job_id,
        )

    async def mark_failed(self, job_id: str, error: str, retry: bool = True) -> None:
        if retry:
            await self._store.execute(
                """UPDATE async_jobs
                   SET state = CASE
                       WHEN retry_count + 1 >= max_retries THEN 'failed'
                       ELSE 'pending'
                   END,
                   retry_count = retry_count + 1,
                   error = $2,
                   scheduled_at = NOW() +
                       (LEAST(retry_count + 1, 5) * INTERVAL '30 seconds')
                   WHERE job_id = $1""",
                job_id,
                error,
            )
        else:
            await self._store.execute(
                """UPDATE async_jobs
                   SET state = 'failed', error = $2, finished_at = NOW()
                   WHERE job_id = $1""",
                job_id,
                error,
            )

    async def listen(self) -> AsyncIterator[str]:
        async for payload in self._store.listen(NOTIFY_CHANNEL):
            yield payload
