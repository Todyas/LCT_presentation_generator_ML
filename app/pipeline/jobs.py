from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


@dataclass
class Job:
    job_id: str
    status: JobStatus = JobStatus.PENDING
    result: Any | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    template_filename: str = ""


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self, template_filename: str = "") -> Job:
        job = Job(job_id=str(uuid.uuid4()), template_filename=template_filename)
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list_all(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    def delete(self, job_id: str) -> Job | None:
        return self._jobs.pop(job_id, None)

    def update(self, job_id: str, **fields: Any) -> None:
        job = self._jobs[job_id]
        for k, v in fields.items():
            setattr(job, k, v)
