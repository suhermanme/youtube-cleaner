from __future__ import annotations

from youtube_cleaner.config import Settings
from youtube_cleaner import executor as executor_module
from youtube_cleaner.executor import Executor
from youtube_cleaner.jobs import JobStore
from youtube_cleaner.models import AccountIdentity, JobState, PlanTarget


class InterruptingExecutor(Executor):
    def _perform(self, job, target):
        raise KeyboardInterrupt


class CompletingExecutor(Executor):
    def _perform(self, job, target):
        pass


def test_ctrl_c_checkpoints_job(tmp_path):
    store = JobStore(tmp_path)
    job = JobState(
        job_id="job",
        account_name="Me",
        account_handle="@me",
        account_url="https://youtube.com/@me",
        operation="remove-liked",
        targets=[PlanTarget(target_id="v:1", title="V", url="/watch?v=v")],
        browser_profile="/tmp/profile",
    )
    store.save(job)
    active = AccountIdentity(authenticated=True, channel_name="Me", channel_handle="@me",
                             channel_url="https://youtube.com/@me")
    summary = InterruptingExecutor(object(), Settings(), store).resume(job, active)
    assert summary.interrupted
    assert summary.remaining == 1
    assert store.load("job").last_error == "Interrupted safely by user"


def test_retry_logic():
    attempts = 0

    def flaky():
        nonlocal attempts
        attempts += 1
        return attempts == 3

    executor = Executor(object(), Settings(retries=2), JobStore.__new__(JobStore))
    executor.page = type("P", (), {"wait_for_timeout": lambda self, ms: None})()
    assert executor._with_retries(flaky)
    assert attempts == 3


def test_resume_limit_processes_only_canary_target(tmp_path):
    store = JobStore(tmp_path)
    targets = [
        PlanTarget(target_id=f"v{index}:1", title=f"V{index}", url=f"/watch?v=v{index}")
        for index in range(2)
    ]
    job = JobState(
        job_id="limited",
        account_name="Me",
        account_handle="@me",
        account_url="https://youtube.com/@me",
        operation="remove-liked",
        targets=targets,
        browser_profile="/tmp/profile",
    )
    store.save(job)
    active = AccountIdentity(authenticated=True, channel_name="Me", channel_handle="@me",
                             channel_url="https://youtube.com/@me")
    page = type("P", (), {"url": "https://youtube.com/", "wait_for_timeout": lambda self, ms: None})()
    summary = CompletingExecutor(page, Settings(action_delay=0), store).resume(job, active, max_targets=1)
    assert summary.completed == 1
    assert summary.remaining == 1


def test_live_progress_reports_only_verified_checkpointed_completion(tmp_path):
    store = JobStore(tmp_path)
    targets = [
        PlanTarget(target_id=f"v{index}:1", title=f"Video {index}", url=f"/watch?v=v{index}")
        for index in range(2)
    ]
    job = JobState(
        job_id="progress",
        account_name="Me",
        account_handle="@me",
        account_url="https://youtube.com/@me",
        operation="remove-liked",
        targets=targets,
        browser_profile="/tmp/profile",
    )
    store.save(job)
    active = AccountIdentity(authenticated=True, channel_name="Me", channel_handle="@me",
                             channel_url="https://youtube.com/@me")
    page = type("P", (), {"url": "https://youtube.com/", "wait_for_timeout": lambda self, ms: None})()
    events = []
    summary = CompletingExecutor(
        page,
        Settings(action_delay=0),
        store,
        progress=events.append,
    ).resume(job, active)

    completed = [event for event in events if event.phase == "completed"]
    assert summary.completed == 2
    assert [event.attempted_this_run for event in completed] == [1, 2]
    assert [event.completed for event in completed] == [1, 2]
    assert [event.remaining for event in completed] == [1, 0]
    assert store.load("progress").completed == ["v0:1", "v1:1"]


def test_absent_target_is_desired_idempotent_state(tmp_path):
    class EmptyLibrary:
        def discover_videos(self):
            return []

    page = type(
        "P", (),
        {"evaluate": lambda self, script: None, "wait_for_timeout": lambda self, ms: None},
    )()
    executor = Executor(page, Settings(max_no_change_attempts=1, max_scrolls=2), JobStore(tmp_path))
    assert executor._scroll_to_video(EmptyLibrary(), "already-gone") is False


def test_batch_reuses_open_watch_later_page(monkeypatch, tmp_path):
    navigations = []

    class Page:
        url = "https://www.youtube.com/"

    class Library:
        def __init__(self, page, *, debug_dir=None):
            pass

        def remove_video(self, video_id, action_key):
            return True

    class ReusingExecutor(Executor):
        def _scroll_to_video(self, library, video_id):
            return True

    page = Page()

    def fake_navigate(current_page, url):
        navigations.append(url)
        current_page.url = url

    monkeypatch.setattr(executor_module, "navigate", fake_navigate)
    monkeypatch.setattr(executor_module, "LibraryPage", Library)
    executor = ReusingExecutor(page, Settings(action_delay=0), JobStore(tmp_path))
    job = JobState(
        job_id="reuse",
        account_name="Me",
        account_handle="@me",
        account_url="https://youtube.com/@me",
        operation="remove-watch-later",
        targets=[],
        browser_profile="/tmp/profile",
    )
    first = PlanTarget(target_id="one:1", title="One", url="/watch?v=one")
    second = PlanTarget(target_id="two:2", title="Two", url="/watch?v=two")

    executor._perform(job, first)
    executor._perform(job, second)

    assert navigations == ["https://www.youtube.com/playlist?list=WL"]


def test_saved_playlist_removal_opens_each_target_and_uses_library_toggle(monkeypatch, tmp_path):
    navigations = []
    removals = []

    class Page:
        url = "https://www.youtube.com/"

    class Playlist:
        def __init__(self, page, *, debug_dir=None):
            pass

        def remove_saved_playlist(self, playlist_id):
            removals.append(playlist_id)
            return True

    page = Page()

    def fake_navigate(current_page, url):
        navigations.append(url)
        current_page.url = url

    monkeypatch.setattr(executor_module, "navigate", fake_navigate)
    monkeypatch.setattr(executor_module, "PlaylistPage", Playlist)
    executor = Executor(page, Settings(action_delay=0), JobStore(tmp_path))
    job = JobState(
        job_id="saved-playlists",
        account_name="Me",
        account_handle="@me",
        account_url="https://youtube.com/@me",
        operation="remove-saved-playlist",
        targets=[],
        browser_profile="/tmp/profile",
    )
    first = PlanTarget(
        target_id="saved-playlist:PLone",
        title="One",
        url="https://youtube.com/playlist?list=PLone",
        metadata={"playlist_id": "PLone"},
    )
    second = PlanTarget(
        target_id="saved-playlist:PLtwo",
        title="Two",
        url="https://youtube.com/playlist?list=PLtwo",
        metadata={"playlist_id": "PLtwo"},
    )

    executor._perform(job, first)
    executor._perform(job, second)

    assert navigations == [
        "https://youtube.com/playlist?list=PLone",
        "https://youtube.com/playlist?list=PLtwo",
    ]
    assert removals == ["PLone", "PLtwo"]


def test_mixed_playlist_cleanup_routes_saved_and_owned_targets(monkeypatch, tmp_path):
    navigations = []
    removals = []
    deletions = []

    class Page:
        url = "https://www.youtube.com/"

    class Playlist:
        def __init__(self, page, *, debug_dir=None):
            pass

        def remove_saved_playlist(self, playlist_id):
            removals.append(playlist_id)
            return True

        def delete_playlist(self, playlist_id):
            deletions.append(playlist_id)
            return True

    page = Page()

    def fake_navigate(current_page, url):
        navigations.append(url)
        current_page.url = url

    monkeypatch.setattr(executor_module, "navigate", fake_navigate)
    monkeypatch.setattr(executor_module, "PlaylistPage", Playlist)
    executor = Executor(page, Settings(action_delay=0), JobStore(tmp_path))
    job = JobState(
        job_id="mixed-playlists",
        account_name="Me",
        account_handle="@me",
        account_url="https://youtube.com/@me",
        operation="clean-playlists",
        targets=[],
        browser_profile="/tmp/profile",
    )
    saved = PlanTarget(
        target_id="playlist-clean:PLsaved",
        title="Saved",
        url="https://youtube.com/playlist?list=PLsaved",
        metadata={"playlist_id": "PLsaved", "cleanup_action": "remove-saved"},
    )
    owned = PlanTarget(
        target_id="playlist-clean:PLowned",
        title="Owned",
        url="https://youtube.com/playlist?list=PLowned",
        metadata={"playlist_id": "PLowned", "cleanup_action": "delete-owned"},
    )

    executor._perform(job, saved)
    executor._perform(job, owned)

    assert navigations == [saved.url, owned.url]
    assert removals == ["PLsaved"]
    assert deletions == ["PLowned"]
