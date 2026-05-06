from __future__ import annotations

import asyncio

from src.infra.protocols import JobQueue
from src.service.kb.service import KBService


class KBIngestWorker:
    """Background worker that processes kb.ingest jobs via LISTEN/NOTIFY.

    Only works with PostgreSQL-backed job queue (not the null/SQLite backend).
    """

    def __init__(
        self,
        *,
        kb: KBService,
        jobs: JobQueue,
        worker_id: str = "kb-ingest-1",
    ) -> None:
        self._kb = kb
        self._jobs = jobs
        self._worker_id = worker_id
        self._wake = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        listen_task = asyncio.create_task(self._listen_loop(stop))
        try:
            while not stop.is_set():
                job = await self._jobs.claim_next(
                    self._worker_id, job_types=["kb.ingest"], lease_sec=600
                )
                if job is None:
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=5)
                    except TimeoutError:
                        pass
                    self._wake.clear()
                    continue

                try:
                    await self._kb.ingest(**job.payload)
                    await self._jobs.mark_done(job.job_id)
                except Exception as e:
                    await self._jobs.mark_failed(job.job_id, str(e))
        finally:
            listen_task.cancel()

    async def _listen_loop(self, stop: asyncio.Event) -> None:
        listener = await self._jobs.listen()
        async for _ in listener:
            if stop.is_set():
                break
            self._wake.set()
