from __future__ import annotations

from collections.abc import Callable

from playwright.sync_api import Page

from .account import detect_active_account
from .config import Settings
from .models import AccountIdentity, ScanResult, ScanStatus
from .navigation import LIKED_VIDEOS, PLAYLISTS, WATCH_LATER, navigate, playlist_url
from .pages import LibraryPage, PlaylistIndexPage
from .pages import playlist_id_from_url
from .scrolling import lazy_collect


def scan_integrity_warning(
    *,
    lazy_complete: bool,
    discovered_ids: set[str],
    rendered_ids: set[str],
    visible_count: int | None,
) -> str | None:
    """Return a conservative warning whenever scan completeness is uncertain."""
    if not lazy_complete:
        return "Lazy loading hit its safety limit; results may be incomplete."
    missing = rendered_ids - discovered_ids
    if missing:
        return (
            f"YouTube rendered {len(rendered_ids)} playlist video links, but the row selectors "
            f"discovered only {len(discovered_ids)}. The UI may have changed."
        )
    if visible_count is not None and visible_count > len(discovered_ids):
        unresolved = visible_count - len(discovered_ids)
        return (
            f"YouTube reports {visible_count} total entries; {len(discovered_ids)} rendered video rows "
            f"were discovered and {unresolved} entries were not rendered as normal video links. "
            "They may be unavailable, private, deleted, or hidden by the current UI."
        )
    if not discovered_ids and visible_count != 0:
        return (
            "No video rows were discovered and the page did not provide a reliable empty-playlist "
            "signal. The UI may have changed."
        )
    return None


def playlist_index_integrity_warning(
    *, lazy_complete: bool, discovered_ids: set[str], rendered_ids: set[str]
) -> str | None:
    if not lazy_complete:
        return "Playlist lazy loading hit its safety limit; results may be incomplete."
    missing = rendered_ids - discovered_ids
    if missing:
        return (
            f"YouTube rendered {len(rendered_ids)} playlist links, but the card selectors discovered "
            f"only {len(discovered_ids)}. The UI may have changed."
        )
    if not discovered_ids:
        return (
            "No playlist cards were discovered and the page did not provide a reliable empty-library "
            "signal. The UI may have changed."
        )
    return None


class Scanner:
    def __init__(self, page: Page, settings: Settings, *, account: AccountIdentity | None = None,
                 debug_dir=None, progress: Callable[[int, int], None] | None = None):
        self.page = page
        self.settings = settings
        self.account = account
        self.debug_dir = debug_dir
        self.progress = progress

    def _account(self) -> AccountIdentity:
        if self.account is None:
            self.account = detect_active_account(self.page)
        return self.account

    def _videos(self, area: str, url: str, *, playlist_name: str | None = None) -> ScanResult:
        account = self._account()
        navigate(self.page, url)
        library = LibraryPage(self.page, debug_dir=self.debug_dir)
        outcome = lazy_collect(
            self.page,
            lambda: library.discover_videos(playlist_name=playlist_name, playlist_url=url),
            lambda video: f"{video.video_id}:{video.position}" if area == "playlist" else video.video_id,
            max_no_change_attempts=self.settings.max_no_change_attempts,
            max_scrolls=self.settings.max_scrolls,
            on_progress=self.progress,
        )
        discovered_ids = {video.video_id for video in outcome.items}
        rendered_ids = library.rendered_video_ids(playlist_id=playlist_id_from_url(url))
        visible_count = library.visible_playlist_count()
        warning = scan_integrity_warning(
            lazy_complete=outcome.complete,
            discovered_ids=discovered_ids,
            rendered_ids=rendered_ids,
            visible_count=visible_count,
        )
        return ScanResult(
            area=area,
            account=account,
            status=ScanStatus.COMPLETE if warning is None else ScanStatus.POSSIBLY_INCOMPLETE,
            videos=outcome.items,
            reported_video_count=visible_count,
            warning=warning,
        )

    def likes(self) -> ScanResult:
        return self._videos("likes", LIKED_VIDEOS, playlist_name="Liked videos")

    def watch_later(self) -> ScanResult:
        return self._videos("watch-later", WATCH_LATER, playlist_name="Watch later")

    def playlist(self, playlist_id: str, title: str | None = None) -> ScanResult:
        return self._videos("playlist", playlist_url(playlist_id), playlist_name=title)

    def playlists(self, *, include_videos: bool = False) -> ScanResult:
        account = self._account()
        navigate(self.page, PLAYLISTS)
        index = PlaylistIndexPage(self.page, debug_dir=self.debug_dir)
        outcome = lazy_collect(
            self.page,
            index.discover_playlists,
            lambda playlist: playlist.playlist_id,
            max_no_change_attempts=self.settings.max_no_change_attempts,
            max_scrolls=self.settings.max_scrolls,
            on_progress=self.progress,
        )
        playlists = outcome.items
        discovered_ids = {playlist.playlist_id for playlist in playlists}
        rendered_ids = index.rendered_playlist_ids()
        warning = playlist_index_integrity_warning(
            lazy_complete=outcome.complete,
            discovered_ids=discovered_ids,
            rendered_ids=rendered_ids,
        )
        if include_videos:
            for playlist in playlists:
                detail = self.playlist(playlist.playlist_id, playlist.title)
                playlist.videos = detail.videos
                playlist.scan_status = detail.status
                playlist.details_scanned = True
                if playlist.video_count is None:
                    playlist.video_count = len(detail.videos)
        return ScanResult(
            area="playlists",
            account=account,
            status=ScanStatus.COMPLETE if warning is None else ScanStatus.POSSIBLY_INCOMPLETE,
            playlists=playlists,
            rendered_playlist_count=len(rendered_ids),
            warning=warning,
        )
