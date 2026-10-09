from __future__ import annotations

import pytest

from youtube_cleaner.config import Settings
from youtube_cleaner import executor as executor_module
from youtube_cleaner.executor import Executor
from youtube_cleaner.jobs import JobStore
from youtube_cleaner.models import (
    AccountIdentity,
    JobState,
    LibraryVideo,
    PlanTarget,
    Playlist,
    ScanResult,
)


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


def test_resume_checkpoints_known_unsupported_target_as_skipped(tmp_path):
    store = JobStore(tmp_path)
    target = PlanTarget(
        target_id="playlist-clean:YS",
        title="Saved Shorts",
        url="https://youtube.com/playlist?list=YS",
        metadata={"playlist_id": "YS", "cleanup_action": "delete-owned"},
    )
    job = JobState(
        job_id="saved-shorts",
        account_name="Me",
        account_handle="@me",
        account_url="https://youtube.com/@me",
        operation="clean-playlists",
        targets=[target],
        failed={target.target_id: "old selector failure"},
        browser_profile="/tmp/profile",
    )
    store.save(job)
    active = AccountIdentity(
        authenticated=True,
        channel_name="Me",
        channel_handle="@me",
        channel_url="https://youtube.com/@me",
    )
    page = type("P", (), {"url": "https://youtube.com/playlist?list=YS"})()

    summary = Executor(page, Settings(action_delay=0), store).resume(job, active)

    saved = store.load(job.job_id)
    assert summary.skipped == 1
    assert summary.failed == 0
    assert summary.remaining == 0
    assert target.target_id in saved.skipped
    assert target.target_id not in saved.failed


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


def test_playlist_cleanup_continuously_discovers_new_ui_batches(monkeypatch, tmp_path):
    account = AccountIdentity(
        authenticated=True,
        channel_name="Me",
        channel_handle="@me",
        channel_url="https://youtube.com/@me",
    )
    one = Playlist(
        playlist_id="PLone",
        title="One",
        playlist_url="https://youtube.com/playlist?list=PLone",
        owner_name="Someone Else",
        owner_url="https://youtube.com/@someone-else",
    )
    two = Playlist(
        playlist_id="PLtwo",
        title="Two",
        playlist_url="https://youtube.com/playlist?list=PLtwo",
        owner_name="Someone Else",
        owner_url="https://youtube.com/@someone-else",
    )
    scans = []

    class BatchScanner:
        def __init__(self, *args, **kwargs):
            pass

        def playlists(self):
            scans.append(True)
            return ScanResult(area="playlists", account=account, playlists=[one, two])

    monkeypatch.setattr(executor_module, "Scanner", BatchScanner)
    target = PlanTarget(
        target_id="playlist-clean:PLone",
        title="One",
        url=one.playlist_url,
        metadata={"playlist_id": "PLone", "cleanup_action": "remove-saved"},
    )
    job = JobState(
        job_id="continuous",
        account_name="Me",
        account_handle="@me",
        account_url="https://youtube.com/@me",
        operation="clean-playlists",
        targets=[target],
        browser_profile="/tmp/profile",
    )
    store = JobStore(tmp_path)
    store.save(job)
    page = type("P", (), {"url": "https://youtube.com/feed/playlists"})()

    summary = CompletingExecutor(
        page,
        Settings(action_delay=0, discovery_convergence_passes=2),
        store,
    ).resume(job, account, discover_more=True)

    saved = store.load(job.job_id)
    assert summary.completed == 2
    assert summary.remaining == 0
    assert saved.completed == ["playlist-clean:PLone", "playlist-clean:PLtwo"]
    assert saved.continuous_discovery
    assert [target.target_id for target in saved.targets] == [
        "playlist-clean:PLone",
        "playlist-clean:PLtwo",
    ]
    assert len(scans) == 3  # one discovery, then two no-new convergence scans


@pytest.mark.parametrize(
    ("operation", "area"),
    [("remove-liked", "likes"), ("remove-watch-later", "watch-later")],
)
def test_video_cleanup_continuously_discovers_new_ui_batches(
    monkeypatch, tmp_path, operation, area
):
    account = AccountIdentity(
        authenticated=True,
        channel_name="Me",
        channel_handle="@me",
        channel_url="https://youtube.com/@me",
    )
    videos = [
        LibraryVideo(video_id="one", title="One", video_url="https://youtube.com/watch?v=one"),
        LibraryVideo(video_id="two", title="Two", video_url="https://youtube.com/watch?v=two"),
    ]
    scans = []

    class BatchScanner:
        def __init__(self, *args, **kwargs):
            pass

        def likes(self):
            scans.append(True)
            return ScanResult(area="likes", account=account, videos=videos)

        def watch_later(self):
            scans.append(True)
            return ScanResult(area="watch-later", account=account, videos=videos)

    monkeypatch.setattr(executor_module, "Scanner", BatchScanner)
    target = PlanTarget(
        target_id="one:1",
        title="One",
        url=videos[0].video_url,
        metadata={"video_id": "one"},
    )
    job = JobState(
        job_id=f"continuous-{area}",
        account_name="Me",
        account_handle="@me",
        account_url="https://youtube.com/@me",
        operation=operation,
        targets=[target],
        browser_profile="/tmp/profile",
    )
    store = JobStore(tmp_path)
    store.save(job)
    page = type("P", (), {"url": "https://youtube.com/playlist?list=LL"})()

    summary = CompletingExecutor(
        page,
        Settings(action_delay=0, discovery_convergence_passes=2),
        store,
    ).resume(job, account, discover_more=True)

    saved = store.load(job.job_id)
    assert summary.completed == 2
    assert {target.metadata["video_id"] for target in saved.targets} == {"one", "two"}
    assert saved.continuous_discovery
    assert len(scans) == 3


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

        def remove_saved_playlist_via_network(self, playlist_id):
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

        def remove_saved_playlist_via_network(self, playlist_id):
            removals.append(playlist_id)
            return True

        def delete_playlist_via_network(self, playlist_id):
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
    stale_generated = PlanTarget(
        target_id="playlist-clean:RDCLAK5uy_mix",
        title="Generated mix",
        url="https://youtube.com/playlist?list=RDCLAK5uy_mix",
        metadata={"playlist_id": "RDCLAK5uy_mix", "cleanup_action": "delete-owned"},
    )

    executor._perform(job, saved)
    executor._perform(job, owned)
    executor._perform(job, stale_generated)

    assert navigations == [saved.url, owned.url, stale_generated.url]
    assert removals == ["PLsaved", "RDCLAK5uy_mix"]
    assert deletions == ["PLowned"]
