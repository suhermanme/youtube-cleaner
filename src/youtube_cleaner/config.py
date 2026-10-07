from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "youtube-cleaner"


def _xdg_path(env: str, fallback: Path) -> Path:
    value = os.environ.get(env)
    return Path(value).expanduser() / APP_NAME if value else fallback / APP_NAME


@dataclass(frozen=True, slots=True)
class Paths:
    data: Path
    state: Path

    @classmethod
    def default(cls) -> "Paths":
        home = Path.home()
        return cls(
            data=_xdg_path("XDG_DATA_HOME", home / ".local" / "share"),
            state=_xdg_path("XDG_STATE_HOME", home / ".local" / "state"),
        )

    @property
    def browser_profile(self) -> Path:
        """Legacy/native Chrome profile path retained for compatibility."""
        return self.data / "browser-profile"

    @property
    def chromium_browser_profile(self) -> Path:
        return self.data / "browser-profile-chromium"

    def browser_profile_for(self, browser: str) -> Path:
        if browser == "chrome":
            return self.browser_profile
        if browser == "chromium":
            return self.chromium_browser_profile
        raise ValueError("browser must be 'chromium' or 'chrome'")

    @property
    def backups(self) -> Path:
        return self.data / "backups"

    @property
    def exports(self) -> Path:
        return self.data / "exports"

    @property
    def jobs(self) -> Path:
        return self.state / "jobs"

    @property
    def plans(self) -> Path:
        return self.state / "plans"

    @property
    def logs(self) -> Path:
        return self.state / "logs"

    @property
    def debug(self) -> Path:
        return self.state / "debug"

    def create(self) -> None:
        for path in (
            self.browser_profile, self.chromium_browser_profile, self.backups, self.exports, self.jobs,
            self.plans, self.logs, self.debug,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(slots=True)
class Settings:
    browser: str = "chromium"
    headless: bool = False
    debug: bool = False
    action_delay: float = 1.5
    timeout_ms: int = 15_000
    navigation_timeout_ms: int = 45_000
    max_no_change_attempts: int = 5
    max_scrolls: int = 500
    retries: int = 2
