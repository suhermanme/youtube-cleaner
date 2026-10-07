from __future__ import annotations

from youtube_cleaner.models import AccountIdentity, LibraryVideo
from youtube_cleaner.pages import playlist_id_from_url, video_id_from_url
from youtube_cleaner.scrolling import lazy_collect
from youtube_cleaner.scanner import playlist_index_integrity_warning, scan_integrity_warning
from youtube_cleaner.cli import _normalize_scan_area


class FakePage:
    def __init__(self):
        self.scrolls = 0

    def evaluate(self, script):
        self.scrolls += 1

    def wait_for_timeout(self, milliseconds):
        pass


def test_video_id_extraction():
    assert video_id_from_url("/watch?v=dQw4w9WgXcQ&list=LL") == "dQw4w9WgXcQ"
    assert video_id_from_url("https://www.youtube.com/shorts/abc123") == "abc123"
    assert video_id_from_url("/feed/playlists") is None


def test_playlist_id_extraction():
    assert playlist_id_from_url("/playlist?list=PL123&si=xyz") == "PL123"
    assert playlist_id_from_url("/watch?v=x&list=PL456") == "PL456"


def test_scan_area_aliases_are_normalized_before_browser_startup():
    assert _normalize_scan_area("playlist") == "playlists"
    assert _normalize_scan_area("liked-videos") == "likes"
    assert _normalize_scan_area("watch_later") == "watch-later"


def test_lazy_loading_discovers_and_terminates():
    page = FakePage()
    batches = [["a"], ["a", "b"], ["a", "b"], ["a", "b"]]

    def discover():
        return batches[min(page.scrolls, len(batches) - 1)]

    result = lazy_collect(page, discover, lambda item: item, max_no_change_attempts=2, max_scrolls=20, pause_ms=0)
    assert result.items == ["a", "b"]
    assert result.complete
    assert page.scrolls < 20


def test_lazy_loading_hard_cap_is_incomplete():
    page = FakePage()
    result = lazy_collect(page, lambda: [str(page.scrolls)], lambda item: item,
                          max_no_change_attempts=5, max_scrolls=3, pause_ms=0)
    assert not result.complete


def test_scan_integrity_rejects_false_complete_zero():
    warning = scan_integrity_warning(
        lazy_complete=True,
        discovered_ids=set(),
        rendered_ids={"visible123"},
        visible_count=None,
    )
    assert warning is not None
    assert "UI may have changed" in warning


def test_scan_integrity_accepts_matching_rendered_rows():
    assert scan_integrity_warning(
        lazy_complete=True,
        discovered_ids={"one", "two"},
        rendered_ids={"one", "two"},
        visible_count=2,
    ) is None


def test_scan_integrity_requires_reliable_empty_signal():
    assert scan_integrity_warning(
        lazy_complete=True,
        discovered_ids=set(),
        rendered_ids=set(),
        visible_count=None,
    ) is not None
    assert scan_integrity_warning(
        lazy_complete=True,
        discovered_ids=set(),
        rendered_ids=set(),
        visible_count=0,
    ) is None


def test_playlist_index_integrity_rejects_false_complete_zero():
    warning = playlist_index_integrity_warning(
        lazy_complete=True,
        discovered_ids=set(),
        rendered_ids={"PLvisible"},
    )
    assert warning is not None
    assert "UI may have changed" in warning


def test_playlist_index_integrity_accepts_matching_cards():
    assert playlist_index_integrity_warning(
        lazy_complete=True,
        discovered_ids={"PLone", "PLtwo"},
        rendered_ids={"PLone", "PLtwo"},
    ) is None
