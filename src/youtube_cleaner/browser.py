from __future__ import annotations

import shutil
import os
import platform
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from .config import Paths, Settings


class BrowserProfileInUse(RuntimeError):
    pass


class ChromeNotFound(RuntimeError):
    pass


def launch_options(settings: Settings) -> dict[str, Any]:
    options: dict[str, Any] = {
        "headless": settings.headless,
        "locale": "en-US",
        "viewport": {"width": 1440, "height": 1000},
        "args": ["--lang=en-US"],
    }
    if settings.browser == "chrome":
        options["channel"] = "chrome"
        # A profile authenticated by normal macOS Chrome uses Chrome's regular
        # Keychain integration. Playwright's testing defaults select a mock
        # keychain/basic password store, making Chrome unable to decrypt its own
        # saved session. The browser still owns all cookie access.
        if platform.system() == "Darwin":
            options["ignore_default_args"] = ["--use-mock-keychain", "--password-store=basic"]
    elif settings.browser != "chromium":
        raise ValueError("browser must be 'chromium' or 'chrome'")
    return options


@dataclass
class BrowserSession:
    paths: Paths
    settings: Settings
    _playwright: Playwright | None = None
    _connected_browser: Browser | None = None
    _native_process: subprocess.Popen | None = None
    _native_browser_pid: int | None = None
    context: BrowserContext | None = None
    page: Page | None = None

    @property
    def profile(self) -> Path:
        return self.paths.browser_profile_for(self.settings.browser)

    def __enter__(self) -> "BrowserSession":
        self.paths.create()
        self._playwright = sync_playwright().start()
        try:
            if self.settings.browser == "chrome":
                self._connect_to_native_chrome()
            else:
                kwargs = launch_options(self.settings)
                self.context = self._playwright.chromium.launch_persistent_context(
                    self.profile, **kwargs
                )
        except Exception as exc:
            if self._native_process and self._native_process.poll() is None:
                self._native_process.terminate()
            self._playwright.stop()
            self._playwright = None
            message = str(exc).lower()
            if any(
                marker in message
                for marker in (
                    "user data directory is already in use",
                    "opening in existing browser session",
                    "profile appears to be in use",
                    "processsingleton",
                )
            ):
                raise BrowserProfileInUse(
                    f"Browser profile is already in use: {self.profile}. "
                    "On macOS, activate the dedicated Chrome window and press Command-Q; "
                    "closing the window alone does not quit Chrome."
                ) from exc
            raise
        self.context.set_default_timeout(self.settings.timeout_ms)
        self.context.set_default_navigation_timeout(self.settings.navigation_timeout_ms)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self

    def _connect_to_native_chrome(self) -> None:
        assert self._playwright is not None
        lock_pid = profile_lock_pid(self.profile)
        if lock_pid is not None:
            raise BrowserProfileInUse(
                f"Dedicated Chrome PID {lock_pid} is already using {self.profile}. "
                "On macOS, activate that Chrome instance and press Command-Q."
            )
        endpoint_file = self.profile / "DevToolsActivePort"
        if endpoint_file.exists():
            endpoint_file.unlink()
        chrome = find_system_chrome()
        self._native_process = subprocess.Popen(
            native_chrome_cdp_arguments(chrome, self.profile),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        port: str | None = None
        while time.monotonic() < deadline:
            if endpoint_file.exists():
                lines = endpoint_file.read_text(encoding="utf-8").splitlines()
                if lines and lines[0].isdigit():
                    port = lines[0]
                    break
            if self._native_process.poll() is not None:
                break
            time.sleep(0.1)
        if port is None:
            raise RuntimeError("Installed Chrome did not publish its local DevTools endpoint")
        self._connected_browser = self._playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{port}", timeout=self.settings.navigation_timeout_ms
        )
        self._native_browser_pid = profile_lock_pid(self.profile)
        if not self._connected_browser.contexts:
            raise RuntimeError("Installed Chrome exposed no browser context")
        self.context = self._connected_browser.contexts[0]

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._connected_browser:
            try:
                cdp = self._connected_browser.new_browser_cdp_session()
                cdp.send("Browser.close")
            except Exception:
                try:
                    self._connected_browser.close()
                except Exception:
                    pass
        elif self.context:
            self.context.close()
        if self._native_process and self._native_process.poll() is None:
            self._native_process.terminate()
        owned_pid = self._native_browser_pid or profile_lock_pid(self.profile)
        if owned_pid is not None:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and profile_lock_pid(self.profile) == owned_pid:
                time.sleep(0.1)
            if profile_lock_pid(self.profile) == owned_pid:
                try:
                    os.kill(owned_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if self._playwright:
            self._playwright.stop()


def reset_profile(profile: Path) -> None:
    """Remove only the application's dedicated profile."""
    if profile.exists():
        shutil.rmtree(profile)


def clear_profile_session(profile: Path) -> None:
    """Logout is profile reset: no cookies are inspected or manipulated."""
    reset_profile(profile)


def find_system_chrome() -> Path:
    """Locate installed stable Chrome without touching its normal profile."""
    candidates: list[Path] = []
    system = platform.system()
    if system == "Darwin":
        candidates.extend(
            [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            ]
        )
    elif system == "Windows":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if root := os.environ.get(variable):
                candidates.append(Path(root) / "Google/Chrome/Application/chrome.exe")
    else:
        for executable in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            if resolved := shutil.which(executable):
                candidates.append(Path(resolved))
    match = next((candidate for candidate in candidates if candidate.is_file()), None)
    if not match:
        raise ChromeNotFound(
            "Installed Google Chrome was not found. Install Chrome or pass --browser chromium "
            "and use the standard Playwright login flow."
        )
    return match


def launch_native_chrome_for_login(profile: Path) -> subprocess.Popen:
    """Launch Chrome without Playwright so Google can authenticate a human.

    The same dedicated profile is later reopened by Playwright. No cookie or
    storage files are read, copied, exported, or modified by application code.
    """
    chrome = find_system_chrome()
    profile.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            str(chrome),
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "https://www.youtube.com/",
        ],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def native_chrome_cdp_arguments(chrome: Path, profile: Path) -> list[str]:
    """Build a normal Chrome launch with a loopback DevTools endpoint.

    There are deliberately no Playwright testing flags or automation-evasion
    flags. Playwright attaches only after Chrome has opened its own profile.
    """
    return [
        str(chrome),
        f"--user-data-dir={profile}",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "https://www.youtube.com/",
    ]


def profile_lock_pid(profile: Path) -> int | None:
    """Return the live Chromium SingletonLock PID without inspecting profile data."""
    lock = profile / "SingletonLock"
    if not lock.is_symlink():
        return None
    try:
        target = os.readlink(lock)
        pid = int(target.rsplit("-", 1)[1])
        os.kill(pid, 0)
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return None
    except PermissionError:
        # Lack of signal permission still proves that the process exists.
        return pid
    return pid
