from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlparse

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from .diagnostics import capture_failure
from .models import LibraryVideo, Playlist, ScanStatus
from .navigation import YOUTUBE_MANAGED_COLLECTION_IDS
from .network import YouTubeNetworkClient
from .selectors import (
    CHANNEL_LINK_SELECTORS,
    LABELS,
    MENU_BUTTON_SELECTORS,
    PLAYLIST_CARD_SELECTORS,
    PLAYLIST_LINK_SELECTORS,
    VIDEO_CONTAINER_SELECTORS,
    VIDEO_LINK_SELECTORS,
    label_pattern,
)

BASE = "https://www.youtube.com"
BLOCK_PATTERNS = re.compile(
    r"captcha|unusual traffic|verify (?:it'?s|your identity)|automated access|"
    r"traffic tidak biasa|verifikasi identitas|akses otomatis",
    re.IGNORECASE,
)


class UIChangedError(RuntimeError):
    pass


class AutomationBlocked(RuntimeError):
    pass


def video_id_from_url(url: str) -> str | None:
    absolute = urljoin(BASE, url)
    parsed = urlparse(absolute)
    if parsed.path == "/watch":
        return parse_qs(parsed.query).get("v", [None])[0]
    if parsed.path.startswith("/shorts/"):
        return parsed.path.split("/shorts/", 1)[1].split("/", 1)[0] or None
    return None


def playlist_id_from_url(url: str) -> str | None:
    return parse_qs(urlparse(urljoin(BASE, url)).query).get("list", [None])[0]


def _first_matching_descendant(container: Locator, selectors: tuple[str, ...]) -> Locator | None:
    for selector in selectors:
        loc = container.locator(selector)
        for index in range(loc.count()):
            candidate = loc.nth(index)
            if candidate.is_visible():
                return candidate
    return None


def _first_text(container: Locator, selectors: tuple[str, ...]) -> tuple[str | None, str | None]:
    candidate = _first_matching_descendant(container, selectors)
    if not candidate:
        return None, None
    href = candidate.get_attribute("href")
    title = candidate.get_attribute("title") or candidate.inner_text()
    return title.strip() if title else None, href


class YouTubePage:
    def __init__(self, page: Page, *, debug_dir=None):
        self.page = page
        self.debug_dir = debug_dir

    def ensure_not_blocked(self) -> None:
        body = self.page.locator("body").inner_text(timeout=5_000)
        if BLOCK_PATTERNS.search(body):
            raise AutomationBlocked(
                "YouTube displayed an authentication, CAPTCHA, unusual-traffic, or automation challenge. "
                "Resolve it manually and resume later."
            )

    def fail(self, message: str, attempted: list[str] | None = None) -> None:
        if self.debug_dir:
            capture_failure(self.page, self.debug_dir, "selector-failure", attempted)
        raise UIChangedError(message)

class LibraryPage(YouTubePage):
    containers = VIDEO_CONTAINER_SELECTORS

    def video_containers(self) -> list[Locator]:
        result: list[Locator] = []
        # A combined selector makes the browser de-duplicate elements matched by
        # multiple fallbacks while preserving genuine repeated playlist rows.
        loc = self.page.locator(", ".join(self.containers))
        for index in range(loc.count()):
            item = loc.nth(index)
            link = _first_matching_descendant(item, VIDEO_LINK_SELECTORS)
            if link and video_id_from_url(link.get_attribute("href") or ""):
                result.append(item)
        return result

    def discover_videos(self, *, playlist_name: str | None = None, playlist_url: str | None = None) -> list[LibraryVideo]:
        videos: list[LibraryVideo] = []
        # Snapshot all rendered rows in one browser-side pass. Repeated
        # Locator RPCs become prohibitively slow for multi-thousand-item lists.
        # This reads only visible DOM attributes/text from the normal page.
        rows = self.page.eval_on_selector_all(
            ", ".join(self.containers),
            """(nodes, selectors) => nodes.map((node, index) => {
                const first = (list) => list.map(s => node.querySelector(s)).find(Boolean);
                const video = first(selectors.video);
                const channel = first(selectors.channel);
                return {
                    position: index + 1,
                    href: video?.getAttribute('href') || null,
                    title: video?.getAttribute('title') || video?.textContent?.trim() || null,
                    channel_name: channel?.textContent?.trim() || null,
                    channel_href: channel?.getAttribute('href') || null,
                    text: node.innerText || ''
                };
            })""",
            {"video": list(VIDEO_LINK_SELECTORS), "channel": list(CHANNEL_LINK_SELECTORS)},
        )
        for row in rows:
            position = row["position"]
            title = row["title"]
            href = row["href"]
            if not href:
                continue
            video_id = video_id_from_url(href)
            if not video_id:
                continue
            channel_name = row["channel_name"]
            channel_href = row["channel_href"]
            text = row["text"].casefold()
            unavailable = any(value in text for value in ("unavailable", "private video", "video tidak tersedia", "video pribadi"))
            videos.append(
                LibraryVideo(
                    video_id=video_id,
                    title=title or "Unavailable video",
                    video_url=urljoin(BASE, href),
                    channel_name=channel_name,
                    channel_url=urljoin(BASE, channel_href) if channel_href else None,
                    playlist_name=playlist_name,
                    playlist_url=playlist_url,
                    position=position,
                    unavailable=unavailable,
                )
            )
        return videos

    def rendered_video_ids(self, *, playlist_id: str | None = None) -> set[str]:
        """Independently inventory ordinary rendered watch links.

        This deliberately does not reuse the row selectors. It is a safety
        cross-check: if YouTube changes its row component, a zero-row scan must
        not be mistaken for a genuinely empty playlist.
        """
        values = self.page.eval_on_selector_all(
            "a[href*='/watch?v=']",
            """(nodes, expectedList) => [...new Set(nodes.map(node => {
                try {
                    const url = new URL(node.getAttribute('href'), location.href);
                    if (expectedList && url.searchParams.get('list') !== expectedList) return null;
                    return url.searchParams.get('v');
                } catch (_) {
                    return null;
                }
            }).filter(Boolean))]""",
            playlist_id,
        )
        return set(values)

    def visible_playlist_count(self) -> int | None:
        pattern = re.compile(r"([\d.,]+)\s+videos?\b", re.IGNORECASE)
        scopes = self.page.locator(
            "ytd-playlist-header-renderer, ytd-playlist-sidebar-primary-info-renderer, #page-header"
        )
        texts = [
            scopes.nth(index).inner_text()
            for index in range(scopes.count())
            if scopes.nth(index).is_visible()
        ]
        texts.append(self.page.locator("body").inner_text())
        for text in texts:
            if match := pattern.search(text):
                return int(re.sub(r"\D", "", match.group(1)))
        return None

    def container_for_video(self, video_id: str) -> Locator | None:
        matches = self.containers_for_video(video_id)
        return matches[0] if matches else None

    def containers_for_video(self, video_id: str) -> list[Locator]:
        # Ask Chromium to narrow the rendered rows first. The previous
        # implementation crossed the Playwright boundary several times for
        # every row, making large-list deletion progressively very slow.
        escaped = video_id.replace("\\", "\\\\").replace("'", "\\'")
        narrowed = ", ".join(
            f"{selector}:has(a[href*='/watch?v={escaped}'])"
            for selector in self.containers
        )
        candidates = self.page.locator(narrowed)
        matches: list[Locator] = []
        for index in range(candidates.count()):
            container = candidates.nth(index)
            link = _first_matching_descendant(container, VIDEO_LINK_SELECTORS)
            if link and video_id_from_url(link.get_attribute("href") or "") == video_id:
                matches.append(container)
        return matches

    def open_video_menu(self, video_id: str, *, occurrence: str = "first") -> None:
        matches = self.containers_for_video(video_id)
        container = matches[-1] if matches and occurrence == "last" else (matches[0] if matches else None)
        if not container:
            self.fail(f"Could not find video {video_id} in the rendered list")
        for label in LABELS["more_actions"]:
            button = container.get_by_role("button", name=re.compile(rf"^{re.escape(label)}", re.I))
            if button.count() and button.first.is_visible():
                button.first.click()
                self.wait_for_menu()
                return
        # Fallback stays within the target row and requires an accessible label.
        for selector in MENU_BUTTON_SELECTORS:
            buttons = container.locator(selector)
            for index in range(buttons.count()):
                button = buttons.nth(index)
                aria = button.get_attribute("aria-label") or ""
                if label_pattern("more_actions", exact=False).search(aria) and button.is_visible():
                    button.click()
                    self.wait_for_menu()
                    return
        self.fail("Could not identify the target video's More actions button", list(MENU_BUTTON_SELECTORS))

    def wait_for_menu(self) -> None:
        menu = self.page.get_by_role("menu")
        try:
            menu.last.wait_for(state="visible", timeout=5_000)
        except PlaywrightTimeoutError:
            # YouTube sometimes omits menu role but retains menuitem roles.
            self.page.get_by_role("menuitem").first.wait_for(state="visible", timeout=3_000)

    def close_menu(self) -> None:
        self.page.keyboard.press("Escape")

    def select_menu_action(self, action_key: str) -> str:
        pattern = label_pattern(action_key)
        candidates = self.page.get_by_role("menuitem", name=pattern)
        visible = []
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            if not candidate.is_visible():
                continue
            # YouTube gives both its outer action renderer and a nested paper
            # item role=menuitem. Choose the outermost semantic menu item so one
            # visible action is not mistaken for two, without relying on index.
            is_outermost = candidate.evaluate(
                "node => !node.parentElement?.closest('[role=menuitem]')"
            )
            if is_outermost:
                visible.append(candidate)
        if len(visible) != 1:
            self.close_menu()
            self.fail(
                f"Expected exactly one visible menu action for {action_key}; found {len(visible)}. Refusing to guess.",
                list(LABELS[action_key]),
            )
        label = visible[0].inner_text().strip()
        visible[0].click()
        return label

    def remove_video(self, video_id: str, action_key: str) -> bool:
        self.ensure_not_blocked()
        before_count = self.visible_playlist_count()
        target = self.container_for_video(video_id)
        if target is None:
            return False
        target_index = target.evaluate(
            """(node, selector) => [...document.querySelectorAll(selector)].indexOf(node)""",
            ", ".join(self.containers),
        )
        if target_index < 0:
            self.fail(f"Target row for {video_id} detached before its action could be verified")
        self.open_video_menu(video_id)
        self.select_menu_action(action_key)
        for _ in range(16):
            current_count = self.visible_playlist_count()
            if before_count is not None and current_count is not None and current_count < before_count:
                return True
            if not any(video.video_id == video_id for video in self.discover_videos()):
                return True
            self.page.wait_for_timeout(500)
        else:
            # Some YouTube layouts retain a stale row even after reloading the
            # same document. Verify in a fresh tab through at least the former
            # rendered position; never trust the click or a toast alone.
            verification_page = self.page.context.new_page()
            try:
                verification_page.goto(self.page.url, wait_until="domcontentloaded")
                verification_page.wait_for_timeout(1_000)
                verification = type(self)(verification_page, debug_dir=self.debug_dir)
                fresh_count = verification.visible_playlist_count()
                if before_count is not None and fresh_count is not None and fresh_count < before_count:
                    return True
                no_change = 0
                previous = -1
                while len(verification.video_containers()) <= target_index and no_change < 5:
                    count = len(verification.video_containers())
                    no_change = no_change + 1 if count == previous else 0
                    previous = count
                    verification_page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                    verification_page.wait_for_timeout(750)
                return verification.container_for_video(video_id) is None
            finally:
                verification_page.close()
        return False

    def remove_playlist_occurrence(self, video_id: str, *, occurrence: str) -> bool:
        """Remove one occurrence while verifying the occurrence count decreases."""
        self.ensure_not_blocked()
        before = len(self.containers_for_video(video_id))
        if before == 0:
            return False
        self.open_video_menu(video_id, occurrence=occurrence)
        prefixes = LABELS["remove_from_playlist"]
        pattern = re.compile(rf"^(?:{'|'.join(re.escape(x) for x in prefixes)})\s+.+$", re.I)
        candidates = self.page.get_by_role("menuitem", name=pattern)
        visible = [candidates.nth(i) for i in range(candidates.count()) if candidates.nth(i).is_visible()]
        if len(visible) != 1:
            self.close_menu()
            if not visible:
                self.fail(
                    "YouTube did not offer a remove-from-playlist action for this item. "
                    "The playlist is likely saved from another creator or is an auto-generated "
                    "album, mix, or channel playlist; only playlists owned by the active profile "
                    "can be cleaned item by item."
                )
            self.fail(f"Expected one visible remove-from-playlist action; found {len(visible)}")
        visible[0].click()
        try:
            self.page.wait_for_function(
                """([id, before]) => [...document.querySelectorAll('a[href*="/watch?v="]')]
                    .filter(a => new URL(a.href, location.href).searchParams.get('v') === id).length < before""",
                arg=[video_id, before],
                timeout=8_000,
            )
        except PlaywrightTimeoutError:
            return len(self.containers_for_video(video_id)) < before
        return True


class PlaylistIndexPage(YouTubePage):
    def discover_playlists(self) -> list[Playlist]:
        result: dict[str, Playlist] = {}
        rows = self.page.eval_on_selector_all(
            ", ".join(PLAYLIST_CARD_SELECTORS),
            """(nodes, selectors) => nodes.filter(node => {
                const style = getComputedStyle(node);
                return node.getClientRects().length && style.display !== 'none' && style.visibility !== 'hidden';
            }).map(node => {
                const first = selectors.map(s => node.querySelector(s)).find(Boolean);
                const owner = node.querySelector(
                    "yt-content-metadata-view-model a[href^='/@'], " +
                    "yt-content-metadata-view-model a[href^='/channel/']"
                );
                return {
                    href: first?.getAttribute('href') || null,
                    title: first?.getAttribute('title') ||
                        first?.closest('h3')?.getAttribute('title') ||
                        first?.textContent?.trim() || null,
                    owner_name: owner?.textContent?.trim() || null,
                    owner_href: owner?.getAttribute('href') || null,
                    text: node.innerText || ''
                };
            })""",
            list(PLAYLIST_LINK_SELECTORS),
        )
        for row in rows:
            href = row["href"]
            if not href:
                continue
            playlist_id = playlist_id_from_url(href)
            # YouTube-managed collections (Likes, Watch Later, Sounds, and
            # Saved Shorts) are not ordinary user-manageable playlists.
            if (
                not playlist_id
                or playlist_id in YOUTUBE_MANAGED_COLLECTION_IDS
                or playlist_id in result
            ):
                continue
            text = row["text"]
            count_match = re.search(r"([\d.,]+)\s+(?:videos?|video)\b", text, re.I)
            if count_match:
                count = int(re.sub(r"\D", "", count_match.group(1)))
            elif re.search(r"\b(?:no videos|tidak ada video)\b", text, re.I):
                count = 0
            else:
                count = None
            result[playlist_id] = Playlist(
                playlist_id=playlist_id,
                title=row["title"] or playlist_id,
                playlist_url=f"{BASE}/playlist?list={playlist_id}",
                video_count=count,
                owner_name=row["owner_name"],
                owner_url=urljoin(BASE, row["owner_href"]) if row["owner_href"] else None,
            )
        return list(result.values())

    def rendered_playlist_ids(self) -> set[str]:
        """Inventory playlist IDs from ordinary rendered links independently."""
        values = self.page.eval_on_selector_all(
            "a[href*='list=']",
            """(nodes, excludedIds) => [...new Set(nodes.filter(node => {
                const style = getComputedStyle(node);
                return node.getClientRects().length && style.display !== 'none' && style.visibility !== 'hidden';
            }).map(node => {
                try {
                    return new URL(node.getAttribute('href'), location.href).searchParams.get('list');
                } catch (_) {
                    return null;
                }
            }).filter(id => id && !excludedIds.includes(id)))]""",
            list(YOUTUBE_MANAGED_COLLECTION_IDS),
        )
        return set(values)


class PlaylistPage(LibraryPage):
    _ABSENT_PLAYLIST_LINES = {
        "the playlist does not exist.",
        "the playlist does not exist",
        "this playlist does not exist.",
        "this playlist does not exist",
        "playlist tidak ada.",
        "playlist tidak ada",
        "playlist ini tidak ada.",
        "playlist ini tidak ada",
    }

    def _playlist_is_absent(self) -> bool:
        lines = {
            line.strip().casefold()
            for line in self.page.locator("body").inner_text(timeout=5_000).splitlines()
            if line.strip()
        }
        return bool(lines & self._ABSENT_PLAYLIST_LINES)

    def _library_toggle_state(self) -> str | None:
        """Return the rendered saved-playlist state, waiting through rerenders."""
        for _ in range(10):
            remove = self.page.get_by_role("button", name=label_pattern("remove_playlist_from_library"))
            visible_remove = [remove.nth(i) for i in range(remove.count()) if remove.nth(i).is_visible()]
            save = self.page.get_by_role("button", name=label_pattern("save_playlist_to_library"))
            visible_save = [save.nth(i) for i in range(save.count()) if save.nth(i).is_visible()]
            if len(visible_remove) == 1 and not visible_save:
                return "saved"
            if len(visible_save) == 1 and not visible_remove:
                return "not-saved"
            self.page.wait_for_timeout(500)
        return None

    def _reload_for_persisted_library_state(self) -> str | None:
        self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_timeout(1_000)
        self.ensure_not_blocked()
        return self._library_toggle_state()

    def remove_saved_playlist(self, expected_playlist_id: str) -> bool:
        """Un-save a playlist and verify the state survives a fresh reload."""
        self.ensure_not_blocked()
        if playlist_id_from_url(self.page.url) != expected_playlist_id:
            self.fail("Browser is not on the expected saved playlist; refusing Library removal")

        state = self._library_toggle_state()
        if state == "not-saved":
            # Do not trust state left by a prior optimistic rerender. Reload so
            # retries and resumed jobs are idempotent against persisted state.
            state = self._reload_for_persisted_library_state()
            if state == "not-saved":
                return True
        if state != "saved":
            self.fail(
                "YouTube did not expose one saved-playlist Library toggle. "
                "The playlist may already be absent or the UI may have changed."
            )

        remove = self.page.get_by_role("button", name=label_pattern("remove_playlist_from_library"))
        visible_remove = [remove.nth(i) for i in range(remove.count()) if remove.nth(i).is_visible()]
        if len(visible_remove) != 1:
            self.fail(f"Expected one Remove from Library button; found {len(visible_remove)}")
        visible_remove[0].click()
        if self._library_toggle_state() != "not-saved":
            return False

        # The toggle changes optimistically before YouTube has persisted the
        # mutation. Give it time to settle, then require a fresh page load to
        # render the not-saved state before checkpointing success.
        self.page.wait_for_timeout(2_000)
        return self._reload_for_persisted_library_state() == "not-saved"

    def remove_saved_playlist_via_network(self, expected_playlist_id: str) -> bool:
        """Remove a Library save via the observed YouTube request and verify it."""
        self.ensure_not_blocked()
        if playlist_id_from_url(self.page.url) != expected_playlist_id:
            self.fail("Browser is not on the expected saved playlist; refusing Library removal")
        if self._reload_for_persisted_library_state() == "not-saved":
            return True
        YouTubeNetworkClient(self.page).remove_saved_playlist(expected_playlist_id)
        self.page.wait_for_timeout(1_000)
        return self._reload_for_persisted_library_state() == "not-saved"

    def delete_playlist(self, expected_playlist_id: str) -> bool:
        """Delete via header menu and an explicit confirmation dialog only."""
        self.ensure_not_blocked()
        if playlist_id_from_url(self.page.url) != expected_playlist_id:
            self.fail("Browser is not on the expected playlist; refusing deletion")
        # The Playlists feed can retain stale cards after deletion. Treat an
        # exact YouTube nonexistence message as the desired idempotent state.
        if self._playlist_is_absent():
            return True
        header_scopes = (
            "ytd-playlist-header-renderer, ytd-playlist-sidebar-primary-info-renderer, "
            "#page-header, yt-flexible-actions-view-model"
        )
        matches = self.page.get_by_role(
            "button", name=label_pattern("more_actions", exact=False)
        )
        buttons = [
            matches.nth(index)
            for index in range(matches.count())
            if matches.nth(index).is_visible()
            and matches.nth(index).evaluate(
                "(node, scopes) => Boolean(node.closest(scopes))", arg=header_scopes
            )
        ]
        if len(buttons) != 1:
            self.fail(f"Expected one playlist-header More actions button; found {len(buttons)}")
        buttons[0].click()
        self.wait_for_menu()
        self.select_menu_action("delete_playlist")
        dialog = self.page.get_by_role("dialog")
        dialog.wait_for(state="visible")
        confirms = dialog.get_by_role("button", name=label_pattern("confirm_delete"))
        visible = [confirms.nth(i) for i in range(confirms.count()) if confirms.nth(i).is_visible()]
        if len(visible) != 1:
            self.fail(f"Expected one delete confirmation button; found {len(visible)}")
        visible[0].click()
        try:
            self.page.wait_for_function(
                "id => new URL(location.href).searchParams.get('list') !== id",
                arg=expected_playlist_id,
                timeout=10_000,
            )
        except PlaywrightTimeoutError:
            return (
                playlist_id_from_url(self.page.url) != expected_playlist_id
                or self._playlist_is_absent()
            )
        return True

    def delete_playlist_via_network(self, expected_playlist_id: str) -> bool:
        """Delete an owned playlist via the observed request and verify absence."""
        self.ensure_not_blocked()
        if playlist_id_from_url(self.page.url) != expected_playlist_id:
            self.fail("Browser is not on the expected playlist; refusing deletion")
        if self._playlist_is_absent():
            return True
        YouTubeNetworkClient(self.page).delete_owned_playlist(expected_playlist_id)
        self.page.wait_for_timeout(1_000)
        self.page.reload(wait_until="domcontentloaded")
        self.page.wait_for_timeout(1_000)
        self.ensure_not_blocked()
        return self._playlist_is_absent()
