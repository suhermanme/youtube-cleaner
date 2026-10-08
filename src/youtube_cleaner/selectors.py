"""Centralized UI labels and semantic selector fallbacks.

CSS selectors here intentionally target YouTube custom-element names and stable
attributes, never DOM position. Destructive menu choices always require an exact
English or Bahasa Indonesia accessible label.
"""
from __future__ import annotations

import re

LABELS: dict[str, tuple[str, ...]] = {
    "account_menu": ("Account menu", "Menu akun"),
    "sign_in": ("Sign in", "Login"),
    "more_actions": ("More actions", "Action menu", "Tindakan lainnya", "Menu tindakan"),
    "remove_from_liked": ("Remove from Liked videos", "Hapus dari video yang disukai"),
    "remove_from_watch_later": ("Remove from Watch later", "Hapus dari Tonton nanti"),
    "remove_from_playlist": ("Remove from", "Hapus dari"),
    "remove_playlist_from_library": (
        "Remove from library",
        "Remove playlist from library",
        "Hapus dari koleksi",
        "Hapus playlist dari koleksi",
    ),
    "save_playlist_to_library": (
        "Save to library",
        "Save playlist",
        "Simpan ke koleksi",
        "Simpan playlist",
    ),
    "delete_playlist": ("Delete playlist", "Hapus playlist"),
    "confirm_delete": ("Delete", "Hapus"),
    "edit_playlist": ("Edit", "Edit"),
}

VIDEO_CONTAINER_SELECTORS = (
    "ytd-playlist-video-renderer",
    "ytd-video-renderer",
    # Current desktop playlist UI (observed October 2026). Keep this semantic
    # custom element centralized here because YouTube rolls layouts gradually.
    "yt-lockup-view-model:has(yt-lockup-metadata-view-model h3 a[href*='/watch?v='])",
    # Avoid selecting both a rich-item wrapper and its newer lockup child.
    "ytd-rich-item-renderer:has(a[href*='/watch?v=']):not(:has(yt-lockup-view-model))",
)
VIDEO_LINK_SELECTORS = (
    "a#video-title[href*='/watch?v=']",
    "yt-lockup-metadata-view-model h3 a[href*='/watch?v=']",
    "a[href*='/watch?v='][title]",
    "a.yt-lockup-metadata-view-model__title[href*='/watch?v=']",
)
CHANNEL_LINK_SELECTORS = (
    "ytd-channel-name a[href]",
    "yt-content-metadata-view-model a[href^='/@']",
    "yt-content-metadata-view-model a[href^='/channel/']",
    "a[href^='/@']",
)
MENU_BUTTON_SELECTORS = (
    "button[aria-label]",
    "yt-icon-button button[aria-label]",
    "ytd-menu-renderer button",
)
PLAYLIST_CARD_SELECTORS = (
    "ytd-grid-playlist-renderer",
    "yt-lockup-view-model:has(a[href*='list='])",
    # Older rich-grid card. Exclude wrappers around the newer lockup so one
    # visible card is never extracted twice.
    "ytd-rich-item-renderer:has(a[href*='list=']):not(:has(yt-lockup-view-model))",
)
PLAYLIST_LINK_SELECTORS = (
    "a#video-title[href*='/playlist?list=']",
    # Modern playlist cards route their visible title through the first video
    # (/watch?...&list=...) rather than directly to /playlist.
    "yt-lockup-metadata-view-model h3 a[href*='list=']",
    "a[href*='/playlist?list='][title]",
    "a.yt-lockup-metadata-view-model__title[href*='/playlist?list=']",
)


def label_pattern(key: str, *, exact: bool = True) -> re.Pattern[str]:
    values = "|".join(re.escape(value) for value in LABELS[key])
    return re.compile(f"^(?:{values})$" if exact else f"(?:{values})", re.IGNORECASE)
