from pathlib import Path

import pytest

from youtube_cleaner import browser
from youtube_cleaner.config import Paths, Settings


def test_find_system_chrome_uses_dedicated_known_paths(monkeypatch, tmp_path):
    executable = tmp_path / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(browser.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(browser.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(browser.Path, "is_file", lambda self: self == executable)
    # The user-local candidate is selected without consulting an everyday profile.
    assert browser.find_system_chrome() == executable


def test_native_login_launches_without_remote_debugging(monkeypatch, tmp_path):
    executable = tmp_path / "chrome"
    executable.touch()
    captured = {}

    class Process:
        pass

    def fake_popen(arguments, **kwargs):
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(browser, "find_system_chrome", lambda: executable)
    monkeypatch.setattr(browser.subprocess, "Popen", fake_popen)
    profile = tmp_path / "dedicated-profile"
    browser.launch_native_chrome_for_login(profile)
    arguments = captured["arguments"]
    assert f"--user-data-dir={profile}" in arguments
    assert not any("remote-debugging" in argument for argument in arguments)
    assert not any("enable-automation" in argument for argument in arguments)
    assert captured["kwargs"]["stdout"] is browser.subprocess.DEVNULL


def test_profile_lock_reports_live_pid(monkeypatch, tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("test-host-4321")
    monkeypatch.setattr(browser.os, "kill", lambda pid, signal: None)
    assert browser.profile_lock_pid(profile) == 4321


def test_profile_lock_ignores_stale_pid(monkeypatch, tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("test-host-4321")

    def missing(pid, signal):
        raise ProcessLookupError

    monkeypatch.setattr(browser.os, "kill", missing)
    assert browser.profile_lock_pid(profile) is None


def test_macos_chrome_uses_real_keychain_for_native_profile(monkeypatch):
    monkeypatch.setattr(browser.platform, "system", lambda: "Darwin")
    options = browser.launch_options(Settings(browser="chrome"))
    assert options["channel"] == "chrome"
    assert "--use-mock-keychain" in options["ignore_default_args"]
    assert "--password-store=basic" in options["ignore_default_args"]


def test_bundled_chromium_keeps_playwright_defaults(monkeypatch):
    monkeypatch.setattr(browser.platform, "system", lambda: "Darwin")
    options = browser.launch_options(Settings(browser="chromium"))
    assert "ignore_default_args" not in options


def test_native_chrome_cdp_launch_has_no_playwright_or_evasion_flags(tmp_path):
    chrome = tmp_path / "chrome"
    profile = tmp_path / "profile"
    arguments = browser.native_chrome_cdp_arguments(chrome, profile)
    assert f"--user-data-dir={profile}" in arguments
    assert "--remote-debugging-port=0" in arguments
    assert not any("enable-automation" in argument for argument in arguments)
    assert not any("mock-keychain" in argument for argument in arguments)
    assert not any("AutomationControlled" in argument for argument in arguments)


def test_chrome_and_bundled_chromium_use_separate_profiles(tmp_path):
    paths = Paths(data=tmp_path / "data", state=tmp_path / "state")
    assert paths.browser_profile_for("chrome") == paths.data / "browser-profile"
    assert paths.browser_profile_for("chromium") == paths.data / "browser-profile-chromium"
    assert paths.browser_profile_for("chrome") != paths.browser_profile_for("chromium")
