from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path


def configure_logging(directory: Path, *, debug: bool = False) -> logging.Logger:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"youtube-cleaner-{datetime.now(UTC):%Y%m%d}.log"
    logger = logging.getLogger("youtube_cleaner")
    if not logger.handlers:
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    return logger
