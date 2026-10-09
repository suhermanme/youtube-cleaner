from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any

from playwright.sync_api import Page


YOUTUBE_ORIGIN = "https://www.youtube.com"


class NetworkMutationError(RuntimeError):
    """A captured YouTube mutation could not be issued or accepted safely."""


def sapisid_authorization(
    cookies: list[dict[str, Any]], *, now: Callable[[], float] = time.time
) -> str:
    """Build YouTube's short-lived authorization value from browser cookies."""
    values = {str(cookie["name"]): str(cookie["value"]) for cookie in cookies}
    timestamp = str(int(now()))
    schemes = (
        ("SAPISIDHASH", "SAPISID"),
        ("SAPISID1PHASH", "__Secure-1PAPISID"),
        ("SAPISID3PHASH", "__Secure-3PAPISID"),
    )
    parts: list[str] = []
    for scheme, cookie_name in schemes:
        if value := values.get(cookie_name):
            digest = hashlib.sha1(
                f"{timestamp} {value} {YOUTUBE_ORIGIN}".encode()
            ).hexdigest()
            parts.append(f"{scheme} {timestamp}_{digest}")
    if not parts:
        raise NetworkMutationError(
            "The active browser session did not expose a SAPISID authentication cookie"
        )
    return " ".join(parts)


class YouTubeNetworkClient:
    """Replay observed YouTube mutations with fresh browser-session metadata."""

    def __init__(self, page: Page, *, timeout_ms: int = 15_000):
        self.page = page
        self.timeout_ms = timeout_ms

    def remove_saved_playlist(self, playlist_id: str) -> None:
        self._post("like/removelike", {"target": {"playlistId": playlist_id}})

    def delete_owned_playlist(self, playlist_id: str) -> None:
        self._post(
            "playlist/delete",
            {
                "playlistId": playlist_id,
                "deletePlaylistContext": "DELETE_PLAYLIST_CONTEXT_LIBRARY",
            },
        )

    def _configuration(self) -> dict[str, Any]:
        config = self.page.evaluate(
            """() => {
                const get = key => globalThis.ytcfg?.get?.(key);
                const context = get('INNERTUBE_CONTEXT');
                return {
                    context,
                    clientName: get('INNERTUBE_CONTEXT_CLIENT_NAME'),
                    clientVersion: get('INNERTUBE_CLIENT_VERSION') || context?.client?.clientVersion,
                    sessionIndex: get('SESSION_INDEX'),
                    visitorData: get('VISITOR_DATA') || context?.client?.visitorData
                };
            }"""
        )
        if not isinstance(config, dict) or not isinstance(config.get("context"), dict):
            raise NetworkMutationError("YouTube did not expose its current request context")
        return config

    def _post(self, endpoint: str, mutation: dict[str, Any]) -> None:
        config = self._configuration()
        cookies = self.page.context.cookies([YOUTUBE_ORIGIN])
        headers = {
            "authorization": sapisid_authorization(cookies),
            "content-type": "application/json",
            "origin": YOUTUBE_ORIGIN,
            "referer": self.page.url,
            "x-goog-authuser": str(config.get("sessionIndex") or 0),
            "x-origin": YOUTUBE_ORIGIN,
            "x-youtube-bootstrap-logged-in": "true",
            "x-youtube-client-name": str(config.get("clientName") or 1),
            "x-youtube-client-version": str(config.get("clientVersion") or ""),
        }
        if visitor_data := config.get("visitorData"):
            headers["x-goog-visitor-id"] = str(visitor_data)
        body = {"context": config["context"], **mutation}
        url = f"{YOUTUBE_ORIGIN}/youtubei/v1/{endpoint}?prettyPrint=false"
        response = self.page.context.request.post(
            url,
            headers=headers,
            data=body,
            timeout=self.timeout_ms,
        )
        if response.ok:
            response.dispose()
            return
        try:
            try:
                detail = response.json().get("error", {}).get("message")
            except Exception:
                detail = None
            suffix = f": {detail}" if detail else ""
            raise NetworkMutationError(
                f"YouTube rejected {endpoint} with HTTP {response.status}{suffix}"
            )
        finally:
            response.dispose()
