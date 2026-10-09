from __future__ import annotations

from dataclasses import dataclass


AREAS = ("likes", "watch-later", "playlists")


@dataclass(frozen=True, slots=True)
class CleanupRequest:
    area: str
    execute: bool
    all_items: bool = True
    limit: int | None = None
    title_contains: str | None = None
    channel_name: str | None = None
    discover_more: bool = False


def build_cleanup_args(request: CleanupRequest) -> list[str]:
    """Translate GUI state into the public CLI contract."""
    if request.area not in AREAS:
        raise ValueError(f"Unknown cleanup area: {request.area}")
    if request.limit is not None and request.limit < 1:
        raise ValueError("Limit must be at least 1")

    title = request.title_contains.strip() if request.title_contains else None
    channel = request.channel_name.strip() if request.channel_name else None
    if not any((request.all_items, request.limit is not None, title, channel)):
        raise ValueError("Select all items, set a limit, or enter a filter")

    unrestricted_all = (
        request.all_items
        and request.limit is None
        and title is None
        and channel is None
    )
    if request.discover_more and not unrestricted_all:
        raise ValueError(
            "Continuous discovery requires All items with no limit or filters"
        )

    args = [request.area, "clean"]
    if request.all_items:
        args.append("--all")
    if request.limit is not None:
        args.extend(("--limit", str(request.limit)))
    if title:
        args.extend(("--title-contains", title))
    if channel:
        args.extend(("--channel-name", channel))
    args.append("--execute" if request.execute else "--dry-run")
    if request.discover_more:
        args.append("--discover-more")
    return args


def build_resume_args(
    job_id: str,
    *,
    limit: int | None = None,
    discover_more: bool = False,
) -> list[str]:
    job_id = job_id.strip()
    if not job_id:
        raise ValueError("Select a job to resume")
    if limit is not None and limit < 1:
        raise ValueError("Resume limit must be at least 1")
    if limit is not None and discover_more:
        raise ValueError("Choose either a resume limit or continuous discovery")

    args = ["jobs", "resume", job_id]
    if limit is not None:
        args.extend(("--limit", str(limit)))
    if discover_more:
        args.append("--discover-more")
    return args
