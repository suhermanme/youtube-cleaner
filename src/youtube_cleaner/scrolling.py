from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from playwright.sync_api import Page

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ScrollOutcome:
    items: list
    complete: bool
    scrolls: int
    no_change_attempts: int


def lazy_collect(
    page: Page,
    discover: Callable[[], list[T]],
    key: Callable[[T], str],
    *,
    max_no_change_attempts: int = 5,
    max_scrolls: int = 500,
    pause_ms: int = 1_000,
    on_progress: Callable[[int, int], None] | None = None,
) -> ScrollOutcome:
    found: dict[str, T] = {}
    no_change = 0
    scrolls = 0
    while no_change < max_no_change_attempts and scrolls < max_scrolls:
        before = len(found)
        for item in discover():
            found.setdefault(key(item), item)
        if len(found) == before:
            no_change += 1
        else:
            no_change = 0
            if on_progress:
                on_progress(len(found), scrolls)
        if no_change >= max_no_change_attempts:
            break
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_for_timeout(pause_ms)
        scrolls += 1
    # Hitting the hard safety cap means we cannot claim completeness.
    complete = scrolls < max_scrolls and no_change >= max_no_change_attempts
    return ScrollOutcome(list(found.values()), complete, scrolls, no_change)
