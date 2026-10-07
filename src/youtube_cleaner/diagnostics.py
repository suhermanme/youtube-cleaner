from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Page


def capture_failure(page: Page, directory: Path, label: str, attempted: list[str] | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    base = directory / f"{label}-{stamp}"
    page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
    # HTML can contain page content, but storage/cookies/headers are never captured.
    base.with_suffix(".html").write_text(page.content(), encoding="utf-8")
    base.with_suffix(".json").write_text(
        json.dumps({"url": page.url, "attempted_selectors": attempted or []}, indent=2),
        encoding="utf-8",
    )
    return base.with_suffix(".png")
