from __future__ import annotations

import os
from pathlib import Path

import pytest

from youtube_cleaner.pages import LibraryPage, PlaylistIndexPage, PlaylistPage


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("YOUTUBE_CLEANER_INTEGRATION") != "1",
                    reason="set YOUTUBE_CLEANER_INTEGRATION=1 for local-browser fixture test")
def test_saved_playlist_fixture_with_real_browser():
    from playwright.sync_api import sync_playwright

    fixture = Path(__file__).parent / "fixtures" / "playlist.html"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{fixture.resolve().as_uri()}?list=PLfixture")
        videos = LibraryPage(page).discover_videos(
            playlist_name="Fixture", playlist_url="https://youtube.com/playlist?list=PLfixture"
        )
        library = LibraryPage(page)
        count = library.visible_playlist_count()
        modern_matches = library.containers_for_video("modern789")
        page.evaluate("localStorage.removeItem('fixture-playlist-saved')")
        page.get_by_role("button", name="Remove from Library").evaluate(
            """button => button.setAttribute(
                'onclick',
                "this.setAttribute('aria-label', 'Save to Library'); " +
                "this.setAttribute('aria-pressed', 'false')"
            )"""
        )
        optimistic_only = PlaylistPage(page).remove_saved_playlist("PLfixture")
        removed_from_library = PlaylistPage(page).remove_saved_playlist("PLfixture")
        already_absent = PlaylistPage(page).remove_saved_playlist("PLfixture")
        deleted_owned = PlaylistPage(page).delete_playlist("PLfixture")
        page.goto(f"{fixture.resolve().as_uri()}?list=PLmissing")
        page.locator("body").evaluate(
            "body => body.replaceChildren(Object.assign(document.createElement('p'), "
            "{textContent: 'The playlist does not exist.'}))"
        )
        already_deleted = PlaylistPage(page).delete_playlist("PLmissing")
        browser.close()
    assert [video.video_id for video in videos] == ["alpha123", "beta456", "modern789"]
    assert [video.channel_name for video in videos] == ["Alpha Channel", "Beta Channel", "Modern Channel"]
    assert count == 3
    assert len(modern_matches) == 1
    assert not optimistic_only
    assert removed_from_library
    assert already_absent
    assert deleted_owned
    assert already_deleted


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("YOUTUBE_CLEANER_INTEGRATION") != "1",
                    reason="set YOUTUBE_CLEANER_INTEGRATION=1 for local-browser fixture test")
def test_modern_playlist_index_fixture_with_real_browser():
    from playwright.sync_api import sync_playwright

    fixture = Path(__file__).parent / "fixtures" / "playlists.html"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(fixture.resolve().as_uri())
        index = PlaylistIndexPage(page)
        playlists = index.discover_playlists()
        rendered_ids = index.rendered_playlist_ids()
        browser.close()
    assert [playlist.playlist_id for playlist in playlists] == ["PLmodern123"]
    assert playlists[0].title == "Modern saved playlist"
    assert playlists[0].owner_name == "Playlist Owner"
    assert playlists[0].owner_url == "https://www.youtube.com/@playlist-owner"
    assert playlists[0].video_count == 19
    assert rendered_ids == {"PLmodern123"}
