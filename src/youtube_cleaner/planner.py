from __future__ import annotations

import secrets
from collections import defaultdict
from pathlib import Path

from .models import AccountIdentity, CleanupPlan, LibraryVideo, PlanTarget, Playlist
from .storage import atomic_write_model, load_model


def select_videos(
    videos: list[LibraryVideo],
    *,
    all_items: bool = False,
    limit: int | None = None,
    title_contains: str | None = None,
    channel_name: str | None = None,
) -> list[LibraryVideo]:
    if not any((all_items, limit, title_contains, channel_name)):
        return []
    selected = videos
    if title_contains:
        needle = title_contains.casefold()
        selected = [video for video in selected if needle in video.title.casefold()]
    if channel_name:
        needle = channel_name.casefold()
        selected = [video for video in selected if video.channel_name and needle in video.channel_name.casefold()]
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        selected = selected[:limit]
    return selected


def create_plan(
    account: AccountIdentity,
    action: str,
    targets: list[PlanTarget],
    browser_profile: Path,
    plans_dir: Path,
    *,
    source_snapshot: str | None = None,
) -> tuple[CleanupPlan, Path]:
    if not account.authenticated or not account.stable_key:
        raise ValueError("A reliably identified active channel is required")
    plan = CleanupPlan(
        plan_id=secrets.token_hex(6),
        account=account,
        action=action,
        targets=targets,
        browser_profile=str(browser_profile),
        source_snapshot=source_snapshot,
    )
    path = plans_dir / f"{plan.plan_id}.json"
    return plan, atomic_write_model(path, plan)


def video_targets(videos: list[LibraryVideo]) -> list[PlanTarget]:
    return [
        PlanTarget(
            target_id=f"{video.video_id}:{video.position or index}",
            title=video.title,
            url=video.video_url,
            playlist_url=video.playlist_url,
            position=video.position,
            metadata={"video_id": video.video_id},
        )
        for index, video in enumerate(videos, 1)
    ]


def find_duplicates(playlist: Playlist) -> dict[str, list[LibraryVideo]]:
    grouped: dict[str, list[LibraryVideo]] = defaultdict(list)
    for video in playlist.videos:
        grouped[video.video_id].append(video)
    return {video_id: items for video_id, items in grouped.items() if len(items) > 1}


def dedup_targets(playlist: Playlist, *, keep: str = "first") -> list[PlanTarget]:
    if keep not in {"first", "last"}:
        raise ValueError("keep must be 'first' or 'last'")
    result = []
    for video_id, occurrences in find_duplicates(playlist).items():
        removed = occurrences[1:] if keep == "first" else occurrences[:-1]
        # Execution repeatedly removes from the safe edge, avoiding stale indexes.
        occurrence = "last" if keep == "first" else "first"
        for ordinal, video in enumerate(removed, 1):
            result.append(
                PlanTarget(
                    target_id=f"{playlist.playlist_id}:{video_id}:duplicate:{ordinal}",
                    title=video.title,
                    url=video.video_url,
                    playlist_url=playlist.playlist_url,
                    position=video.position,
                    metadata={
                        "video_id": video_id,
                        "playlist_id": playlist.playlist_id,
                        "occurrence": occurrence,
                    },
                )
            )
    return result


def load_plan(plans_dir: Path, plan_id: str) -> CleanupPlan:
    return load_model(plans_dir / f"{plan_id}.json", CleanupPlan)
