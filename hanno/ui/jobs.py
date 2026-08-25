"""In-memory registry of running mining jobs, keyed by seed_id, so
POST /api/mine can return immediately and a status endpoint can report
progress -- with zero changes to ExcavatorPipeline. The ledger (append-only,
already the single source of truth for `hanno trace`) is what the frontend
actually polls for live candidate data; this registry only answers "is a
job running for this seed, and did it error."

Not persisted: restarting `hanno ui` forgets job status, but every
candidate a job already produced stays in the ledger regardless.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Coroutine

from hanno.ledger.models import now_iso


@dataclass
class MiningJob:
    seed_id: str
    task: asyncio.Task
    started_at: str
    error: str | None = None


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, MiningJob] = {}

    def is_running(self, seed_id: str) -> bool:
        job = self._jobs.get(seed_id)
        return job is not None and not job.task.done()

    def start(self, seed_id: str, coro: Coroutine[Any, Any, Any]) -> MiningJob:
        if self.is_running(seed_id):
            raise ValueError(f"a mining job is already running for seed {seed_id}")

        task = asyncio.create_task(coro)
        job = MiningJob(seed_id=seed_id, task=task, started_at=now_iso())
        self._jobs[seed_id] = job

        def _on_done(t: asyncio.Task, job: MiningJob = job) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                job.error = str(exc)

        task.add_done_callback(_on_done)
        return job

    def status(self, seed_id: str) -> dict[str, Any]:
        job = self._jobs.get(seed_id)
        if job is None:
            return {"running": False, "started_at": None, "error": None}
        return {
            "running": not job.task.done(),
            "started_at": job.started_at,
            "error": job.error,
        }
