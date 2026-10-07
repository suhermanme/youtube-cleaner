from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .models import AccountIdentity, Playlist, ScanResult

if TYPE_CHECKING:
    from .executor import ExecutionProgress

console = Console()


class JobProgressDisplay:
    """Render executor events without coupling browser logic to Rich."""

    def __init__(self) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[detail]}", markup=False),
            console=console,
            refresh_per_second=8,
            transient=False,
        )
        self._task_id = None

    def __enter__(self) -> "JobProgressDisplay":
        self._progress.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._progress.stop()

    def update(self, event: "ExecutionProgress") -> None:
        if self._task_id is None:
            self._task_id = self._progress.add_task(
                "Cleanup",
                total=max(1, event.targets_this_run),
                completed=event.attempted_this_run,
                detail="Preparing…",
            )
        target = ""
        if event.current_title:
            compact = " ".join(event.current_title.split())
            target = f" · {compact[:60]}{'…' if len(compact) > 60 else ''}"
        detail = (
            f"{event.phase}: {event.message}{target} | "
            f"completed={event.completed} failed={event.failed} "
            f"skipped={event.skipped} remaining={event.remaining}"
        )
        self._progress.update(
            self._task_id,
            total=max(1, event.targets_this_run),
            completed=event.attempted_this_run,
            detail=detail,
            refresh=True,
        )


def show_account(account: AccountIdentity, profile: str | None = None) -> None:
    console.print("[bold]Active YouTube profile detected[/bold]")
    console.print(f"  Profile name: {account.channel_name or '[unknown]'}")
    console.print(f"  Handle (safety ID): {account.channel_handle or '[unknown]'}")
    if profile:
        console.print(f"  Browser profile: {profile}")


def show_scan(result: ScanResult) -> None:
    console.print(f"\n[bold]{result.area.replace('-', ' ').title()}[/bold]")
    if result.area == "playlists":
        console.print(f"  Playlists discovered: {len(result.playlists):,}")
        if (
            result.rendered_playlist_count is not None
            and result.rendered_playlist_count != len(result.playlists)
        ):
            console.print(f"  Playlist IDs visible in rendered links: {result.rendered_playlist_count:,}")
        detailed = [playlist for playlist in result.playlists if playlist.details_scanned]
        if detailed:
            empty = [p for p in detailed if p.scan_status.value == "complete" and not p.videos]
            duplicate_entries = sum(
                len(p.videos) - len({video.video_id for video in p.videos}) for p in detailed
            )
            console.print(f"  Empty playlists: {len(empty):,}")
            console.print(f"  Duplicate playlist entries: {duplicate_entries:,}")
    else:
        console.print(f"  Videos discovered: {len(result.videos):,}")
        if result.reported_video_count is not None:
            console.print(f"  YouTube page total: {result.reported_video_count:,}")
            unresolved = result.reported_video_count - len(result.videos)
            if unresolved > 0:
                console.print(f"  Unresolved/non-rendered entries: {unresolved:,}")
    console.print(f"  Scan status: {result.status.value}")
    if result.warning:
        console.print(f"  [yellow]{result.warning}[/yellow]")


def show_videos(result: ScanResult) -> None:
    table = Table(title=result.area.replace("-", " ").title())
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("Channel")
    table.add_column("Video ID")
    for index, video in enumerate(result.videos, 1):
        table.add_row(str(index), video.title, video.channel_name or "", video.video_id)
    console.print(table)
    show_scan(result)


def show_playlists(playlists: list[Playlist]) -> None:
    table = Table(title="Playlists")
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("Videos", justify="right")
    table.add_column("Playlist ID")
    for index, playlist in enumerate(playlists, 1):
        count = "?" if playlist.video_count is None else f"{playlist.video_count:,}"
        table.add_row(str(index), playlist.title, count, playlist.playlist_id)
    console.print(table)
