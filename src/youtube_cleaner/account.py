from __future__ import annotations

import re
import time
from urllib.parse import urljoin

from playwright.sync_api import Page

from .models import AccountIdentity
from .navigation import YOUTUBE_HOME, navigate
from .selectors import LABELS, label_pattern

HANDLE_RE = re.compile(r"^@[\w.\-]+$", re.UNICODE)


class AccountDetectionError(RuntimeError):
    pass


class AutomatedLoginBlocked(AccountDetectionError):
    pass


LOGIN_BLOCKED_RE = re.compile(
    r"browser or app may not be secure|couldn['’]t sign you in|"
    r"browser atau aplikasi ini mungkin tidak aman|tidak dapat membuat anda login",
    re.IGNORECASE,
)


def _first_visible(locator):
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if candidate.is_visible():
            return candidate
    return None


def account_menu_button(page: Page):
    button = _first_visible(page.get_by_role("button", name=label_pattern("account_menu")))
    if button:
        return button
    # Semantic attribute fallback; exact labels remain centralized.
    for label in LABELS["account_menu"]:
        candidate = _first_visible(page.locator(f'button[aria-label="{label}"]'))
        if candidate:
            return candidate
    return None


def is_authenticated(page: Page) -> bool:
    return account_menu_button(page) is not None


def detect_active_account(page: Page) -> AccountIdentity:
    """Read the active channel from YouTube's visible account menu.

    A handle or channel URL is mandatory; a display name alone is not a safe
    identity key and therefore raises instead of permitting destructive work.
    """
    if "youtube.com" not in page.url:
        navigate(page, YOUTUBE_HOME)
    button = account_menu_button(page)
    if not button:
        return AccountIdentity(authenticated=False)

    avatar_url = button.locator("img").first.get_attribute("src") if button.locator("img").count() else None
    button.click()
    try:
        menus = page.locator("ytd-multi-page-menu-renderer, tp-yt-iron-dropdown")
        menu = None
        for index in range(menus.count() - 1, -1, -1):
            candidate = menus.nth(index)
            if candidate.is_visible():
                menu = candidate
                break
        if menu is None:
            raise AccountDetectionError("The account menu opened, but its visible contents could not be identified")

        # The account panel shell can become visible before YouTube hydrates its
        # identity text/links. Wait for semantic identity content, not a fixed
        # animation delay.
        for _ in range(20):
            menu_text = menu.inner_text()
            if menu.locator("a[href*='/@'], a[href*='/channel/']").count() or re.search(
                r"(?<![\w.])@[\w.\-]+", menu_text
            ):
                break
            page.wait_for_timeout(250)

        # Current desktop UI exposes the active channel header and/or an @handle link.
        header = menu.locator("ytd-active-account-header-renderer")
        scope = header if header.count() else menu
        name = None
        for selector in ("#account-name", "yt-formatted-string#account-name", "a[href^='/@']"):
            loc = scope.locator(selector)
            if loc.count() and loc.first.is_visible():
                text = loc.first.inner_text().strip()
                if text and not HANDLE_RE.match(text):
                    name = text
                    break

        handle = None
        channel_url = None
        for link in scope.locator("a[href*='/@'], a[href*='/channel/']").all():
            if not link.is_visible():
                continue
            href = link.get_attribute("href")
            text = link.inner_text().strip()
            if text.startswith("@"):
                handle = text.split()[0]
            if href:
                channel_url = urljoin("https://www.youtube.com", href.split("?")[0])
                if not handle and "/@" in href:
                    handle = "@" + href.split("/@", 1)[1].split("/", 1)[0]
            if handle and channel_url:
                break

        if not handle:
            # Newer account-menu variants may render the identity in generic
            # elements rather than yt-formatted-string/span. Read only the
            # visible menu's rendered text and reject @ fragments inside email
            # addresses via the negative lookbehind.
            match = re.search(r"(?<![\w.])@[\w.\-]+", scope.inner_text())
            handle = match.group(0) if match else None
        if handle and not channel_url:
            channel_url = f"https://www.youtube.com/{handle}"
        if not handle or not channel_url:
            raise AccountDetectionError(
                "Authenticated, but the active YouTube profile handle could not be read reliably. "
                "No destructive operation is allowed."
            )
        return AccountIdentity(
            authenticated=True,
            channel_name=name or handle,
            channel_handle=handle,
            channel_url=channel_url,
            avatar_url=avatar_url,
        )
    finally:
        page.keyboard.press("Escape")


def wait_for_manual_login(page: Page, *, timeout_seconds: int = 900) -> AccountIdentity:
    if page.url != YOUTUBE_HOME:
        navigate(page, YOUTUBE_HOME)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if is_authenticated(page):
            return detect_active_account(page)
        if "accounts.google." in page.url:
            visible_text = page.locator("body").inner_text(timeout=5_000)
            if LOGIN_BLOCKED_RE.search(visible_text):
                raise AutomatedLoginBlocked(
                    "Google refused sign-in from the Playwright-controlled browser. "
                    "If you already completed native Chrome login, rerun your intended command with "
                    "the global option before the subcommand: youtube-cleaner --browser chrome <command>. "
                    "Otherwise first use: youtube-cleaner browser login --native-chrome"
                )
        page.wait_for_timeout(1000)
    raise TimeoutError("Timed out waiting for manual YouTube authentication")


def require_matching_account(expected: AccountIdentity, active: AccountIdentity) -> None:
    if not expected.matches(active):
        raise AccountDetectionError(
            f"Account mismatch: expected {expected.channel_handle or expected.channel_url}, "
            f"active {active.channel_handle or active.channel_url or 'unknown'}"
        )
