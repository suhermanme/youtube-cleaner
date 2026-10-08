from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field


def utc_now() -> datetime:
    return datetime.now(UTC)


class ScanStatus(StrEnum):
    COMPLETE = "complete"
    POSSIBLY_INCOMPLETE = "possibly incomplete"


class AccountIdentity(BaseModel):
    authenticated: bool = False
    channel_name: str | None = None
    channel_handle: str | None = None
    channel_url: str | None = None
    avatar_url: str | None = None

    @property
    def stable_key(self) -> str | None:
        if self.channel_handle:
            return self.channel_handle.casefold()
        if self.channel_url:
            return self.channel_url.rstrip("/").casefold()
        return None

    def matches(self, other: "AccountIdentity") -> bool:
        return bool(self.authenticated and other.authenticated and self.stable_key and self.stable_key == other.stable_key)


class LibraryVideo(BaseModel):
    video_id: str
    title: str
    video_url: str
    channel_name: str | None = None
    channel_url: str | None = None
    playlist_name: str | None = None
    playlist_url: str | None = None
    position: int | None = None
    unavailable: bool = False
    discovered_at: datetime = Field(default_factory=utc_now)


class Playlist(BaseModel):
    playlist_id: str
    title: str
    playlist_url: str
    video_count: int | None = None
    videos: list[LibraryVideo] = Field(default_factory=list)
    owner_name: str | None = None
    owner_url: str | None = None
    scan_status: ScanStatus = ScanStatus.COMPLETE
    details_scanned: bool = False


class ScanResult(BaseModel):
    area: str
    account: AccountIdentity
    scanned_at: datetime = Field(default_factory=utc_now)
    status: ScanStatus = ScanStatus.COMPLETE
    videos: list[LibraryVideo] = Field(default_factory=list)
    reported_video_count: int | None = None
    playlists: list[Playlist] = Field(default_factory=list)
    rendered_playlist_count: int | None = None
    warning: str | None = None


class PlanTarget(BaseModel):
    target_id: str
    title: str
    url: str
    playlist_url: str | None = None
    position: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CleanupPlan(BaseModel):
    plan_id: str
    created_at: datetime = Field(default_factory=utc_now)
    account: AccountIdentity
    action: str
    targets: list[PlanTarget]
    browser_profile: str
    source_snapshot: str | None = None


class JobState(BaseModel):
    job_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    account_name: str | None = None
    account_handle: str | None = None
    account_url: str | None = None
    operation: str
    targets: list[PlanTarget]
    completed: list[str] = Field(default_factory=list)
    failed: dict[str, str] = Field(default_factory=dict)
    skipped: dict[str, str] = Field(default_factory=dict)
    browser_profile: str
    last_page: str | None = None
    last_error: str | None = None

    @computed_field
    @property
    def remaining(self) -> list[PlanTarget]:
        # Failed targets remain resumable. They are recorded separately so the
        # user can inspect the error, but only success or an explicit skip
        # removes a target from the remaining queue.
        done = set(self.completed) | set(self.skipped)
        return [target for target in self.targets if target.target_id not in done]
