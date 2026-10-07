from __future__ import annotations

from urllib.parse import quote

from playwright.sync_api import Page

YOUTUBE_HOME = "https://www.youtube.com/"
LIKED_VIDEOS = "https://www.youtube.com/playlist?list=LL"
WATCH_LATER = "https://www.youtube.com/playlist?list=WL"
PLAYLISTS = "https://www.youtube.com/feed/playlists"


def playlist_url(playlist_id: str) -> str:
    return f"https://www.youtube.com/playlist?list={quote(playlist_id, safe='')}"


def navigate(page: Page, url: str) -> None:
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(500)
