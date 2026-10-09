from __future__ import annotations

from urllib.parse import urlparse

from .models import AccountIdentity, PlanTarget, Playlist
from .navigation import is_youtube_generated_playlist, is_youtube_managed_collection


def playlist_write_block_reason(
    playlist: Playlist, account: AccountIdentity
) -> str | None:
    """Explain playlist ownership cases that are definitely not editable."""
    if is_youtube_generated_playlist(playlist.playlist_id):
        return "it is an auto-generated YouTube playlist"

    owner_path = urlparse(playlist.owner_url or "").path.rstrip("/").casefold()
    active_paths = {
        urlparse(account.channel_url or "").path.rstrip("/").casefold(),
        f"/{(account.channel_handle or '').casefold()}",
    } - {"", "/"}
    if owner_path and owner_path not in active_paths:
        return f"it is owned by {playlist.owner_name or playlist.owner_url!r}, not the active profile"

    owner = " ".join((playlist.owner_name or "").split()).casefold()
    active_name = " ".join((account.channel_name or "").split()).casefold()
    if not owner_path and owner and active_name and owner not in {active_name, "you", "anda"}:
        return f"it is owned by {playlist.owner_name!r}, not the active profile {account.channel_name!r}"
    return None


def playlist_cleanup_targets(
    playlists: list[Playlist], account: AccountIdentity
) -> list[PlanTarget]:
    """Build safe cleanup targets from one rendered playlist-feed batch."""
    targets: list[PlanTarget] = []
    for playlist in playlists:
        if is_youtube_managed_collection(playlist.playlist_id):
            continue
        external = playlist_write_block_reason(playlist, account) is not None
        targets.append(
            PlanTarget(
                target_id=f"playlist-clean:{playlist.playlist_id}",
                title=playlist.title,
                url=playlist.playlist_url,
                metadata={
                    "playlist_id": playlist.playlist_id,
                    "cleanup_action": "remove-saved" if external else "delete-owned",
                },
            )
        )
    return targets
