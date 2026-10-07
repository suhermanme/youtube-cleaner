from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from .models import ScanResult


BACKUP_WARNING = (
    "A backup is a record of the previous state. It does NOT guarantee automatic "
    "restoration because browser automation may not recreate every previous state perfectly."
)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def export_json(result: ScanResult, directory: Path, *, backup: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.area}-{_stamp()}.json"
    payload = result.model_dump(mode="json")
    payload["backup_warning"] = BACKUP_WARNING
    payload["kind"] = "pre-change-backup" if backup else "export"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def export_csv(result: ScanResult, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.area}-{_stamp()}.csv"
    fields = [
        "video_id", "title", "video_url", "channel_name", "channel_url",
        "playlist_name", "playlist_url", "position", "unavailable", "discovered_at",
    ]
    videos = list(result.videos)
    for playlist in result.playlists:
        videos.extend(playlist.videos)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for video in videos:
            writer.writerow({key: getattr(video, key) for key in fields})
    return path
