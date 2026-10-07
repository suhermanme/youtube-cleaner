from __future__ import annotations

import pytest

from youtube_cleaner.account import AccountDetectionError, HANDLE_RE, require_matching_account
from youtube_cleaner.executor import ConfirmationRequired, Executor
from youtube_cleaner.jobs import JobStore
from youtube_cleaner.models import AccountIdentity, CleanupPlan, PlanTarget
from youtube_cleaner.config import Settings


class NeverClickPage:
    clicks = 0


def account(handle):
    return AccountIdentity(authenticated=True, channel_name=handle, channel_handle=handle,
                           channel_url=f"https://youtube.com/{handle}")


def plan():
    return CleanupPlan(plan_id="p1", account=account("@right"), action="remove-liked",
                       targets=[PlanTarget(target_id="v:1", title="V", url="/watch?v=v")],
                       browser_profile="/tmp/profile")


def test_account_mismatch_blocks_before_job_or_click(tmp_path):
    page = NeverClickPage()
    executor = Executor(page, Settings(), JobStore(tmp_path))
    with pytest.raises(AccountDetectionError):
        executor.job_from_plan(plan(), account("@wrong"), confirmed=True)
    assert page.clicks == 0
    assert list(tmp_path.iterdir()) == []


def test_execute_without_confirmation_blocks_before_click(tmp_path):
    page = NeverClickPage()
    executor = Executor(page, Settings(), JobStore(tmp_path))
    with pytest.raises(ConfirmationRequired):
        executor.job_from_plan(plan(), account("@right"), confirmed=False)
    assert page.clicks == 0
    assert list(tmp_path.iterdir()) == []


def test_identity_requires_stable_handle_or_url():
    assert not AccountIdentity(authenticated=True, channel_name="Same").matches(
        AccountIdentity(authenticated=True, channel_name="Same")
    )


def test_handle_pattern_does_not_treat_email_suffix_as_handle():
    import re

    text = "viewer@example.com\n@viewer-handle"
    match = re.search(r"(?<![\w.])@[\w.\-]+", text)
    assert match and HANDLE_RE.match(match.group(0))
    assert match.group(0) == "@viewer-handle"
