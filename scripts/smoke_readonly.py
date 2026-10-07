"""Launch the browser against YouTube without authenticating or modifying data."""
from __future__ import annotations

import tempfile
from pathlib import Path

from youtube_cleaner.browser import BrowserSession
from youtube_cleaner.config import Paths, Settings
from youtube_cleaner.navigation import YOUTUBE_HOME, navigate


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="youtube-cleaner-smoke-") as temporary:
        root = Path(temporary)
        paths = Paths(data=root / "data", state=root / "state")
        with BrowserSession(paths, Settings(headless=True)) as session:
            assert session.page is not None
            navigate(session.page, YOUTUBE_HOME)
            print(f"url={session.page.url}")
            print(f"title={session.page.title()}")
            print("destructive_actions=0")


if __name__ == "__main__":
    main()
