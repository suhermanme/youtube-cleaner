from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .models import CleanupPlan, JobState
from .storage import atomic_write_model, load_model


class JobStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def path(self, job_id: str) -> Path:
        return self.directory / f"{job_id}.json"

    def create(self, plan: CleanupPlan) -> JobState:
        stamp = plan.created_at.strftime("%Y%m%d-%H%M%S")
        job = JobState(
            job_id=f"{plan.action}-{stamp}-{plan.plan_id[:6]}",
            account_name=plan.account.channel_name,
            account_handle=plan.account.channel_handle,
            account_url=plan.account.channel_url,
            operation=plan.action,
            targets=plan.targets,
            browser_profile=plan.browser_profile,
        )
        self.save(job)
        return job

    def save(self, job: JobState) -> Path:
        job.updated_at = datetime.now(UTC)
        return atomic_write_model(self.path(job.job_id), job)

    def load(self, job_id: str) -> JobState:
        return load_model(self.path(job_id), JobState)

    def list(self) -> list[JobState]:
        if not self.directory.exists():
            return []
        jobs = [load_model(path, JobState) for path in self.directory.glob("*.json")]
        return sorted(jobs, key=lambda job: job.updated_at, reverse=True)
