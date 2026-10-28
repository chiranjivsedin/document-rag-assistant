"""In-memory registry of ingestion jobs: at most one runs at a time; clients poll a job by its id.

Every read and write goes through one lock, so concurrent requests can't start two jobs or see a
half-updated one. The history lives only in this server process: it is lost when the server
restarts, and each uvicorn worker process would keep its own, so run the API with a single worker.
A shared deployment would keep jobs in a database table or a queue instead.
"""

import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal

from rag.ingestion.pipeline import IngestReport

MAX_FINISHED_JOBS = 50  # older finished jobs are forgotten, so memory doesn't grow without limit


@dataclass(frozen=True)
class IngestJob:
    id: str
    status: Literal["running", "succeeded", "failed"]
    started_at: datetime
    finished_at: datetime | None = None
    report: IngestReport | None = None
    error: str | None = None  # a message safe to show to clients


class JobAlreadyRunning(Exception):
    def __init__(self, job: IngestJob):
        super().__init__(f"Ingestion job {job.id} is already running")
        self.job = job


class IngestJobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, IngestJob] = {}  # insertion order = start order
        self._running: str | None = None

    def start(self) -> IngestJob:
        """Register a new running job, or raise JobAlreadyRunning if one is still going."""
        with self._lock:
            if self._running is not None:
                raise JobAlreadyRunning(self._jobs[self._running])
            job = IngestJob(id=uuid.uuid4().hex, status="running", started_at=_now())
            self._jobs[job.id] = job
            self._running = job.id
            self._forget_old_jobs()
            return job

    def succeed(self, job_id: str, report: IngestReport) -> None:
        self._finish(job_id, status="succeeded", report=report)

    def fail(self, job_id: str, error: str) -> None:
        self._finish(job_id, status="failed", error=error)

    def get(self, job_id: str) -> IngestJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _finish(self, job_id: str, **changes) -> None:
        with self._lock:
            self._jobs[job_id] = replace(self._jobs[job_id], finished_at=_now(), **changes)
            if self._running == job_id:
                self._running = None

    def _forget_old_jobs(self) -> None:
        finished = [job_id for job_id, job in self._jobs.items() if job.status != "running"]
        for job_id in finished[:max(0, len(finished) - MAX_FINISHED_JOBS)]:
            del self._jobs[job_id]


def _now() -> datetime:
    return datetime.now(timezone.utc)
