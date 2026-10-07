from __future__ import annotations

from youtube_cleaner.jobs import JobStore
from youtube_cleaner.models import AccountIdentity, CleanupPlan, PlanTarget


def make_plan():
    return CleanupPlan(
        plan_id="abc123",
        account=AccountIdentity(authenticated=True, channel_name="Me", channel_handle="@me",
                                channel_url="https://youtube.com/@me"),
        action="remove-liked",
        targets=[PlanTarget(target_id="v1:1", title="Video", url="https://youtube.com/watch?v=v1")],
        browser_profile="/tmp/profile",
    )


def test_job_serialization_and_resume_state(tmp_path):
    store = JobStore(tmp_path)
    job = store.create(make_plan())
    assert len(job.remaining) == 1
    job.completed.append("v1:1")
    store.save(job)
    restored = store.load(job.job_id)
    assert restored.completed == ["v1:1"]
    assert restored.remaining == []


def test_job_list_newest_first(tmp_path):
    store = JobStore(tmp_path)
    job = store.create(make_plan())
    assert store.list()[0].job_id == job.job_id


def test_failed_target_remains_resumable(tmp_path):
    store = JobStore(tmp_path)
    job = store.create(make_plan())
    job.failed["v1:1"] = "temporary selector failure"
    store.save(job)
    restored = store.load(job.job_id)
    assert [target.target_id for target in restored.remaining] == ["v1:1"]
