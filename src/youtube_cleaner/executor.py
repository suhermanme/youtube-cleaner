from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from playwright.sync_api import Page

from .account import require_matching_account
from .config import Settings
from .jobs import JobStore
from .models import AccountIdentity, CleanupPlan, JobState, PlanTarget
from .navigation import LIKED_VIDEOS, WATCH_LATER, navigate
from .pages import (
    AutomationBlocked,
    LibraryPage,
    PlaylistPage,
    UIChangedError,
    playlist_id_from_url,
)


class ConfirmationRequired(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionSummary:
    completed: int
    failed: int
    skipped: int
    remaining: int
    interrupted: bool = False


@dataclass(frozen=True, slots=True)
class ExecutionProgress:
    job_id: str
    operation: str
    phase: str
    message: str
    attempted_this_run: int
    targets_this_run: int
    completed: int
    failed: int
    skipped: int
    remaining: int
    current_target_id: str | None = None
    current_title: str | None = None


class Executor:
    def __init__(
        self,
        page: Page,
        settings: Settings,
        jobs: JobStore,
        *,
        logger: logging.Logger | None = None,
        debug_dir=None,
        progress: Callable[[ExecutionProgress], None] | None = None,
    ):
        self.page = page
        self.settings = settings
        self.jobs = jobs
        self.logger = logger or logging.getLogger("youtube_cleaner")
        self.debug_dir = debug_dir
        self.progress = progress
        self._progress_job: JobState | None = None
        self._progress_target: PlanTarget | None = None
        self._attempted_this_run = 0
        self._targets_this_run = 0
        self._fully_loaded_playlists: set[str] = set()

    def _emit(
        self,
        job: JobState,
        *,
        phase: str,
        message: str,
        target: PlanTarget | None = None,
    ) -> None:
        if self.progress is None:
            return
        event = ExecutionProgress(
            job_id=job.job_id,
            operation=job.operation,
            phase=phase,
            message=message,
            attempted_this_run=self._attempted_this_run,
            targets_this_run=self._targets_this_run,
            completed=len(job.completed),
            failed=len(job.failed),
            skipped=len(job.skipped),
            remaining=len(job.remaining),
            current_target_id=target.target_id if target else None,
            current_title=target.title if target else None,
        )
        try:
            self.progress(event)
        except Exception as exc:
            # A terminal-rendering failure must never alter cleanup/job state.
            self.logger.warning("progress_callback_failed error=%s", exc)

    def _emit_current(self, phase: str, message: str) -> None:
        if self._progress_job is not None:
            self._emit(
                self._progress_job,
                phase=phase,
                message=message,
                target=self._progress_target,
            )

    def job_from_plan(self, plan: CleanupPlan, active: AccountIdentity, *, confirmed: bool) -> JobState:
        # These checks occur before job creation and, critically, before UI actions.
        require_matching_account(plan.account, active)
        if not confirmed:
            raise ConfirmationRequired("Explicit confirmation is required")
        return self.jobs.create(plan)

    def execute_plan(self, plan: CleanupPlan, active: AccountIdentity, *, confirmed: bool) -> tuple[JobState, ExecutionSummary]:
        job = self.job_from_plan(plan, active, confirmed=confirmed)
        return job, self.resume(job, active)

    def resume(self, job: JobState, active: AccountIdentity, *, max_targets: int | None = None) -> ExecutionSummary:
        expected = AccountIdentity(
            authenticated=True,
            channel_name=job.account_name,
            channel_handle=job.account_handle,
            channel_url=job.account_url,
        )
        require_matching_account(expected, active)
        interrupted = False
        try:
            pending = list(job.remaining)
            if max_targets is not None:
                if max_targets < 1:
                    raise ValueError("max_targets must be at least 1")
                pending = pending[:max_targets]
            self._targets_this_run = len(pending)
            self._attempted_this_run = 0
            self._progress_job = job
            self._progress_target = None
            self._emit(job, phase="starting", message="Preparing cleanup pass")
            for target in pending:
                self._progress_target = target
                self._emit(job, phase="working", message="Starting target", target=target)
                try:
                    self._perform(job, target)
                    job.completed.append(target.target_id)
                    job.failed.pop(target.target_id, None)
                    job.last_error = None
                    self.logger.info("operation=%s target=%s result=completed", job.operation, target.target_id)
                except AutomationBlocked as exc:
                    job.last_error = str(exc)
                    job.last_page = self.page.url
                    self.jobs.save(job)
                    self._emit(job, phase="blocked", message=str(exc), target=target)
                    raise
                except (UIChangedError, Exception) as exc:
                    # A failed target is checkpointed. Selector ambiguity raises
                    # UIChangedError and stops to avoid repeating unsafe guesses.
                    job.failed[target.target_id] = str(exc)
                    job.last_error = str(exc)
                    self.logger.error("operation=%s target=%s result=failed error=%s", job.operation, target.target_id, exc)
                    job.last_page = self.page.url
                    self.jobs.save(job)
                    self._attempted_this_run += 1
                    self._emit(job, phase="failed", message=str(exc), target=target)
                    if isinstance(exc, UIChangedError):
                        break
                    if self._attempted_this_run < self._targets_this_run:
                        self._emit(job, phase="pacing", message="Waiting before the next target", target=target)
                        self._pace()
                    continue
                job.last_page = self.page.url
                self.jobs.save(job)
                self._attempted_this_run += 1
                self._emit(
                    job,
                    phase="completed",
                    message="Removal verified and checkpoint saved",
                    target=target,
                )
                if self._attempted_this_run < self._targets_this_run:
                    self._emit(job, phase="pacing", message="Waiting before the next target", target=target)
                    self._pace()
        except KeyboardInterrupt:
            interrupted = True
            job.last_error = "Interrupted safely by user"
            self.jobs.save(job)
            self._emit(job, phase="interrupted", message=job.last_error, target=self._progress_target)
        finally:
            self._progress_target = None
        return ExecutionSummary(
            completed=len(job.completed),
            failed=len(job.failed),
            skipped=len(job.skipped),
            remaining=len(job.remaining),
            interrupted=interrupted,
        )

    def _perform(self, job: JobState, target: PlanTarget) -> None:
        video_id = str(target.metadata.get("video_id", target.target_id.split(":", 1)[0]))
        if job.operation == "remove-liked":
            self._ensure_playlist_open(LIKED_VIDEOS, "Liked videos")
            library = LibraryPage(self.page, debug_dir=self.debug_dir)
            if not self._scroll_to_video(library, video_id):
                self._emit_current("verified", "Target is already absent")
                return
            self._emit_current("removing", "Removing from Likes and verifying the UI change")
            if not self._with_retries(lambda: library.remove_video(video_id, "remove_from_liked")):
                raise RuntimeError("Removal could not be verified")
        elif job.operation == "remove-watch-later":
            self._ensure_playlist_open(WATCH_LATER, "Watch Later")
            library = LibraryPage(self.page, debug_dir=self.debug_dir)
            if not self._scroll_to_video(library, video_id):
                self._emit_current("verified", "Target is already absent")
                return
            self._emit_current("removing", "Removing from Watch Later and verifying the UI change")
            if not self._with_retries(lambda: library.remove_video(video_id, "remove_from_watch_later")):
                raise RuntimeError("Removal could not be verified")
        elif job.operation in {"remove-playlist-item", "deduplicate-playlist"}:
            if not target.playlist_url:
                raise ValueError("Playlist target has no playlist URL")
            playlist_id = playlist_id_from_url(target.playlist_url)
            self._ensure_playlist_open(target.playlist_url, "playlist")
            playlist = PlaylistPage(self.page, debug_dir=self.debug_dir)
            if job.operation == "deduplicate-playlist":
                if not playlist_id or playlist_id not in self._fully_loaded_playlists:
                    self._scroll_to_end(playlist)
                    if playlist_id:
                        self._fully_loaded_playlists.add(playlist_id)
            else:
                self._scroll_to_video(playlist, video_id)
            occurrence = str(target.metadata.get("occurrence", "first"))
            self._emit_current("removing", "Removing playlist entry and verifying the UI change")
            if not self._with_retries(lambda: playlist.remove_playlist_occurrence(video_id, occurrence=occurrence)):
                raise RuntimeError("Playlist removal could not be verified")
        elif job.operation == "remove-saved-playlist":
            playlist_id = str(target.metadata["playlist_id"])
            self._ensure_playlist_open(target.url, "saved playlist")
            playlist = PlaylistPage(self.page, debug_dir=self.debug_dir)
            self._emit_current("removing", "Removing saved playlist from the Library and verifying the UI change")
            if not self._with_retries(lambda: playlist.remove_saved_playlist(playlist_id)):
                raise RuntimeError("Saved playlist removal could not be verified")
        elif job.operation == "clean-playlists":
            playlist_id = str(target.metadata["playlist_id"])
            cleanup_action = str(target.metadata["cleanup_action"])
            if cleanup_action == "remove-saved":
                self._ensure_playlist_open(target.url, "saved playlist")
                playlist = PlaylistPage(self.page, debug_dir=self.debug_dir)
                self._emit_current(
                    "removing",
                    "Removing saved playlist from the Library and verifying the UI change",
                )
                if not self._with_retries(lambda: playlist.remove_saved_playlist(playlist_id)):
                    raise RuntimeError("Saved playlist removal could not be verified")
            elif cleanup_action == "delete-owned":
                self._emit_current("opening", "Opening owned playlist")
                navigate(self.page, target.url)
                playlist = PlaylistPage(self.page, debug_dir=self.debug_dir)
                self._emit_current("removing", "Permanently deleting owned playlist and verifying navigation")
                if not self._with_retries(lambda: playlist.delete_playlist(playlist_id)):
                    raise RuntimeError("Playlist deletion could not be verified")
            else:
                raise ValueError(f"Unsupported playlist cleanup action: {cleanup_action}")
        elif job.operation == "delete-playlist":
            playlist_id = str(target.metadata["playlist_id"])
            self._emit_current("opening", "Opening playlist")
            navigate(self.page, target.url)
            playlist = PlaylistPage(self.page, debug_dir=self.debug_dir)
            self._emit_current("removing", "Deleting playlist and verifying navigation")
            if not self._with_retries(lambda: playlist.delete_playlist(playlist_id)):
                raise RuntimeError("Playlist deletion could not be verified")
        else:
            raise ValueError(f"Unsupported operation: {job.operation}")

    def _ensure_playlist_open(self, url: str, label: str) -> None:
        """Navigate only when the required rendered playlist is not already open.

        Keeping a verified playlist page alive between adjacent targets avoids
        a full YouTube reload for every removal. The playlist ID comparison is
        based on the ordinary visible URL, not browser storage or backend data.
        """
        expected = playlist_id_from_url(url)
        current = playlist_id_from_url(self.page.url)
        if expected and current == expected:
            self._emit_current("opening", f"Reusing the open {label} page")
            return
        self._emit_current("opening", f"Opening {label}")
        navigate(self.page, url)

    def _scroll_to_video(self, library: LibraryPage, video_id: str) -> bool:
        no_change = 0
        previous = -1
        for _ in range(self.settings.max_scrolls):
            rendered = library.discover_videos()
            if any(video.video_id == video_id for video in rendered):
                self._emit_current("searching", f"Found target after loading {len(rendered):,} rows")
                return True
            count = len(rendered)
            self._emit_current("searching", f"Searching for target; {count:,} rows currently loaded")
            no_change = no_change + 1 if count == previous else 0
            if no_change >= self.settings.max_no_change_attempts:
                return False
            previous = count
            self.page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            self.page.wait_for_timeout(750)
        raise RuntimeError(f"Target video {video_id} search hit the scrolling safety limit")

    def _scroll_to_end(self, library: LibraryPage) -> None:
        no_change = 0
        previous = -1
        for _ in range(self.settings.max_scrolls):
            count = len(library.discover_videos())
            self._emit_current("searching", f"Loading full playlist; {count:,} rows currently loaded")
            no_change = no_change + 1 if count == previous else 0
            if no_change >= self.settings.max_no_change_attempts:
                return
            previous = count
            self.page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
            self.page.wait_for_timeout(750)
        raise RuntimeError("Playlist did not finish lazy-loading before the safety limit")

    def _with_retries(self, action) -> bool:
        for attempt in range(self.settings.retries + 1):
            try:
                if action():
                    return True
            except UIChangedError:
                raise
            except Exception:
                if attempt >= self.settings.retries:
                    raise
                self._emit_current(
                    "retrying",
                    f"Action failed; retry {attempt + 1} of {self.settings.retries}",
                )
                self.page.wait_for_timeout(750 * (attempt + 1))
            else:
                if attempt < self.settings.retries:
                    self._emit_current(
                        "retrying",
                        f"Verification did not succeed; retry {attempt + 1} of {self.settings.retries}",
                    )
        return False

    def _pace(self) -> None:
        delay = max(0.0, self.settings.action_delay)
        time.sleep(random.uniform(delay * 0.8, delay * 1.2))
