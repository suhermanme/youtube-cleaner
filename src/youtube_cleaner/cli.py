from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import typer
from rich.prompt import IntPrompt, Prompt

from .account import (
    AutomatedLoginBlocked,
    AccountDetectionError,
    account_menu_button,
    detect_active_account,
    wait_for_manual_login,
)
from .backup import BACKUP_WARNING, export_csv, export_json
from .browser import (
    BrowserProfileInUse,
    BrowserSession,
    launch_native_chrome_for_login,
    profile_lock_pid,
    reset_profile,
)
from .config import Paths, Settings
from .diagnostics import capture_failure
from .executor import AutomationBlocked, ConfirmationRequired, Executor
from .jobs import JobStore
from .logging_setup import configure_logging
from .models import AccountIdentity, PlanTarget, Playlist, ScanResult
from .navigation import LIKED_VIDEOS, WATCH_LATER, YOUTUBE_HOME, navigate
from .pages import LibraryPage
from .planner import (
    create_plan, dedup_targets, find_duplicates, load_plan, select_videos, video_targets,
)
from .scanner import Scanner
from .ui import JobProgressDisplay, console, show_account, show_playlists, show_scan, show_videos

app = typer.Typer(help="Safely inspect and clean YouTube through its visible web UI.", invoke_without_command=True)
browser_app = typer.Typer(help="Manage the dedicated browser profile.")
likes_app = typer.Typer(help="Scan, export, or clean Liked videos.")
watch_app = typer.Typer(help="Scan, export, or clean Watch Later.")
playlists_app = typer.Typer(help="Inspect and clean playlists.")
jobs_app = typer.Typer(help="Inspect and resume checkpointed jobs.")
debug_app = typer.Typer(help="Selector diagnostics.")
export_app = typer.Typer(help="Export library snapshots.")
plan_app = typer.Typer(help="Create account-bound cleanup plans.")
app.add_typer(browser_app, name="browser")
app.add_typer(likes_app, name="likes")
app.add_typer(watch_app, name="watch-later")
app.add_typer(playlists_app, name="playlists")
app.add_typer(jobs_app, name="jobs")
app.add_typer(debug_app, name="debug")
app.add_typer(export_app, name="export")
app.add_typer(plan_app, name="plan")


@dataclass(slots=True)
class Runtime:
    paths: Paths
    settings: Settings

    @property
    def browser_profile(self) -> Path:
        return self.paths.browser_profile_for(self.settings.browser)


def _runtime(ctx: typer.Context) -> Runtime:
    return ctx.ensure_object(Runtime)


def _authenticated_session(runtime: Runtime):
    session = BrowserSession(runtime.paths, runtime.settings)
    try:
        session.__enter__()
    except BrowserProfileInUse as exc:
        raise typer.BadParameter(str(exc)) from None
    try:
        assert session.page is not None
        navigate(session.page, YOUTUBE_HOME)
        account = detect_active_account(session.page)
        if not account.authenticated:
            if runtime.settings.headless:
                raise AccountDetectionError("Initial login requires a visible browser; omit --headless")
            if runtime.settings.browser == "chrome":
                raise AccountDetectionError(
                    "The dedicated installed-Chrome profile is not authenticated. Do not enter your "
                    "password in the controlled window. Close it, then run: "
                    "youtube-cleaner browser login --native-chrome. After verification, rerun normal "
                    "commands with --browser chrome before the subcommand."
                )
            console.print("\n[bold yellow]Please sign into the YouTube account you want to clean.[/bold yellow]")
            console.print("Complete Google authentication in the browser.")
            console.print("Return here when the YouTube home page is visible.\n")
            account = wait_for_manual_login(session.page)
        show_account(account, str(runtime.browser_profile))
        return session, account
    except AutomatedLoginBlocked as exc:
        session.__exit__(None, None, None)
        raise typer.BadParameter(str(exc)) from None
    except AccountDetectionError as exc:
        if runtime.settings.debug and session.page is not None:
            try:
                button = account_menu_button(session.page)
                if button:
                    button.click()
                    session.page.wait_for_timeout(500)
                diagnostic = capture_failure(session.page, runtime.paths.debug, "account-detection", [])
                console.print(f"Account diagnostic: {diagnostic}")
            except Exception:
                pass
        session.__exit__(None, None, None)
        raise typer.BadParameter(str(exc)) from None
    except Exception:
        session.__exit__(None, None, None)
        raise


def _close(session: BrowserSession) -> None:
    session.__exit__(None, None, None)


SCAN_AREA_ALIASES = {
    "like": "likes",
    "liked": "likes",
    "liked-videos": "likes",
    "playlist": "playlists",
    "watchlater": "watch-later",
    "watch_later": "watch-later",
}


def _normalize_scan_area(area: str) -> str:
    normalized = area.strip().casefold()
    normalized = SCAN_AREA_ALIASES.get(normalized, normalized)
    if normalized not in {"likes", "watch-later", "playlists"}:
        raise typer.BadParameter(
            f"unknown scan area {area!r}; use likes, watch-later, or playlists"
        )
    return normalized


@app.callback()
def main(
    ctx: typer.Context,
    browser: Annotated[str, typer.Option("--browser", help="chromium or installed chrome")] = "chromium",
    headless: Annotated[bool, typer.Option("--headless", help="Advanced: run without a visible browser")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Capture HTML/screenshots when selectors fail")] = False,
    delay: Annotated[float, typer.Option("--delay", min=0.0, help="Seconds between destructive actions")] = 1.5,
) -> None:
    paths = Paths.default()
    ctx.obj = Runtime(paths, Settings(browser=browser, headless=headless, debug=debug, action_delay=delay))
    if ctx.invoked_subcommand is None:
        _interactive(ctx.obj)


def _interactive(runtime: Runtime) -> None:
    console.print("\n[bold cyan]YouTube Cleaner[/bold cyan]")
    session, account = _authenticated_session(runtime)
    try:
        choices = {
            1: "Scan library", 2: "Liked Videos", 3: "Watch Later", 4: "Playlists",
            5: "Find duplicate playlist entries", 6: "Find empty playlists",
            7: "List cleanup jobs", 8: "Export library snapshot", 9: "Keep browser open", 0: "Exit",
        }
        for key, value in choices.items():
            console.print(f"  {key}. {value}")
        choice = IntPrompt.ask("Choose an action", choices=[str(x) for x in choices], default=1)
        scanner = Scanner(session.page, runtime.settings, account=account,
                          debug_dir=runtime.paths.debug if runtime.settings.debug else None,
                          progress=lambda count, _: console.print(f"Discovered: {count:,}", end="\r"))
        if choice == 1:
            # A library overview inventories playlist cards only. Opening every
            # playlist is intentionally reserved for explicit detail commands.
            for result in (scanner.likes(), scanner.watch_later(), scanner.playlists()):
                show_scan(result)
        elif choice == 2:
            show_videos(scanner.likes())
        elif choice == 3:
            show_videos(scanner.watch_later())
        elif choice == 4:
            show_playlists(scanner.playlists().playlists)
        elif choice in {5, 6}:
            result = scanner.playlists(include_videos=True)
            if choice == 5:
                _print_duplicates(result.playlists)
            else:
                _print_empty(result.playlists)
        elif choice == 7:
            _print_jobs(JobStore(runtime.paths.jobs))
        elif choice == 8:
            for result in (scanner.likes(), scanner.watch_later(), scanner.playlists(include_videos=True)):
                console.print(f"Exported: {export_json(result, runtime.paths.exports)}")
        elif choice == 9:
            Prompt.ask("Browser is open. Press Enter here to close it", default="")
    finally:
        _close(session)


@app.command("account")
def account_command(ctx: typer.Context) -> None:
    session, account = _authenticated_session(_runtime(ctx))
    _close(session)


@browser_app.command("login")
def browser_login(
    ctx: typer.Context,
    native_chrome: Annotated[
        bool,
        typer.Option("--native-chrome", help="Authenticate in normal installed Chrome, then verify with Playwright"),
    ] = False,
) -> None:
    runtime = _runtime(ctx)
    if runtime.settings.headless:
        raise typer.BadParameter("browser login cannot run headless")
    if native_chrome:
        runtime.paths.create()
        chrome_profile = runtime.paths.browser_profile_for("chrome")
        console.print("Opening installed Chrome without Playwright control.")
        console.print(f"Dedicated profile: {chrome_profile}")
        launch_native_chrome_for_login(chrome_profile)
        console.print("\n1. Sign in to YouTube manually in that Chrome window.")
        console.print("2. Confirm the intended YouTube channel is active.")
        console.print("3. Completely quit that dedicated Chrome window.")
        Prompt.ask("After Chrome has completely quit, press Enter", default="")
        lock_pid = profile_lock_pid(chrome_profile)
        if lock_pid is not None:
            console.print(
                f"[yellow]Dedicated Chrome is still running as PID {lock_pid}. "
                "On macOS, activate it and press Command-Q.[/yellow]"
            )
            Prompt.ask("After Chrome has completely quit, press Enter again", default="")
        lock_pid = profile_lock_pid(chrome_profile)
        if lock_pid is not None:
            raise typer.BadParameter(
                f"Dedicated Chrome PID {lock_pid} is still using the profile. "
                "Quit it completely and rerun this command."
            )
        original_browser = runtime.settings.browser
        runtime.settings.browser = "chrome"
        try:
            session, account = _authenticated_session(runtime)
            _close(session)
        finally:
            runtime.settings.browser = original_browser
        console.print(f"[green]Native Chrome login verified for {account.channel_handle}.[/green]")
        console.print("Use [bold]--browser chrome[/bold] for later scans and cleanup with this login profile.")
        return
    session, _ = _authenticated_session(runtime)
    _close(session)
    console.print("[green]Login session saved in the dedicated browser profile.[/green]")


def _reset_browser(runtime: Runtime, label: str) -> None:
    console.print(f"Selected browser: {runtime.settings.browser}")
    console.print(f"This removes only: {runtime.browser_profile}")
    if Prompt.ask(f"Type RESET BROWSER to {label}") != "RESET BROWSER":
        console.print("Cancelled.")
        return
    reset_profile(runtime.browser_profile)
    console.print("Dedicated browser profile removed. This cannot be recovered by this application.")


@browser_app.command("logout")
def browser_logout(ctx: typer.Context) -> None:
    console.print("Logout is implemented by deleting the dedicated profile; cookies are never inspected.")
    _reset_browser(_runtime(ctx), "log out")


@browser_app.command("reset")
def browser_reset(ctx: typer.Context) -> None:
    _reset_browser(_runtime(ctx), "reset the browser")


def _scan(runtime: Runtime, area: str, *, include_videos: bool = False) -> ScanResult:
    # Validate before launching a browser. Besides producing a clearer error,
    # this prevents an invalid command from needlessly entering the login flow.
    area = _normalize_scan_area(area)
    session, account = _authenticated_session(runtime)
    try:
        scanner = Scanner(session.page, runtime.settings, account=account,
                          debug_dir=runtime.paths.debug if runtime.settings.debug else None,
                          progress=lambda count, _: console.print(f"Discovered: {count:,}", end="\r"))
        if area == "likes":
            return scanner.likes()
        if area == "watch-later":
            return scanner.watch_later()
        if area == "playlists":
            return scanner.playlists(include_videos=include_videos)
        raise AssertionError(f"unreachable scan area: {area}")
    finally:
        _close(session)


@app.command("scan")
def scan_command(ctx: typer.Context, area: Annotated[str | None, typer.Argument()] = None) -> None:
    runtime = _runtime(ctx)
    areas = [_normalize_scan_area(area)] if area else ["likes", "watch-later", "playlists"]
    for selected in areas:
        show_scan(_scan(runtime, selected))
    console.print("\n[bold cyan]Read-only scan complete. No YouTube data was modified.[/bold cyan]")
    if areas == ["playlists"]:
        console.print(
            "To preview playlist cleanup, run: "
            "[bold]youtube-cleaner --browser chrome playlists clean --all --dry-run[/bold]"
        )


def _area_scan_command(ctx: typer.Context, area: str, *, list_items: bool = False) -> ScanResult:
    result = _scan(_runtime(ctx), area)
    show_videos(result) if list_items else show_scan(result)
    return result


@likes_app.command("scan")
def likes_scan(ctx: typer.Context) -> None:
    _area_scan_command(ctx, "likes")


@likes_app.command("list")
def likes_list(ctx: typer.Context) -> None:
    _area_scan_command(ctx, "likes", list_items=True)


@watch_app.command("scan")
def watch_scan(ctx: typer.Context) -> None:
    _area_scan_command(ctx, "watch-later")


@watch_app.command("list")
def watch_list(ctx: typer.Context) -> None:
    _area_scan_command(ctx, "watch-later", list_items=True)


def _export_area(runtime: Runtime, area: str, fmt: str) -> None:
    result = _scan(runtime, area, include_videos=area == "playlists")
    path = export_csv(result, runtime.paths.exports) if fmt == "csv" else export_json(result, runtime.paths.exports)
    console.print(f"Exported: {path}")
    console.print(f"[yellow]{BACKUP_WARNING}[/yellow]")


for _name, _area in (("likes", "likes"), ("watch-later", "watch-later"), ("playlists", "playlists")):
    def _make_export(area_name: str):
        def command(ctx: typer.Context, format: Annotated[str, typer.Option("--format")] = "json") -> None:
            _export_area(_runtime(ctx), area_name, format)
        return command
    export_app.command(_name)(_make_export(_area))


@likes_app.command("export")
def likes_export(ctx: typer.Context, format: Annotated[str, typer.Option("--format")] = "json") -> None:
    _export_area(_runtime(ctx), "likes", format)


@watch_app.command("export")
def watch_export(ctx: typer.Context, format: Annotated[str, typer.Option("--format")] = "json") -> None:
    _export_area(_runtime(ctx), "watch-later", format)


def _build_area_plan(runtime: Runtime, area: str, *, all_items: bool, limit: int | None,
                     title_contains: str | None, channel_name: str | None):
    result = _scan(runtime, area)
    selected = select_videos(result.videos, all_items=all_items, limit=limit,
                             title_contains=title_contains, channel_name=channel_name)
    if not selected:
        raise typer.BadParameter("No targets selected. Use --all or a filter.")
    action = "remove-liked" if area == "likes" else "remove-watch-later"
    snapshot = export_json(result, runtime.paths.backups, backup=True)
    plan, path = create_plan(result.account, action, video_targets(selected), runtime.browser_profile,
                             runtime.paths.plans, source_snapshot=str(snapshot))
    return plan, path, result


def _plan_options(
    ctx: typer.Context, area: str, all_items: bool, limit: int | None,
    title_contains: str | None, channel_name: str | None,
) -> None:
    runtime = _runtime(ctx)
    plan, path, _ = _build_area_plan(runtime, area, all_items=all_items, limit=limit,
                                      title_contains=title_contains, channel_name=channel_name)
    console.print(f"Plan: {path}\nChannel: {plan.account.channel_handle}\nAction: {plan.action}\nTargets: {len(plan.targets):,}")


@plan_app.command("likes")
def plan_likes(ctx: typer.Context, all_items: Annotated[bool, typer.Option("--all")] = False,
               limit: Annotated[int | None, typer.Option("--limit")] = None,
               title_contains: Annotated[str | None, typer.Option("--title-contains")] = None,
               channel_name: Annotated[str | None, typer.Option("--channel-name")] = None) -> None:
    _plan_options(ctx, "likes", all_items, limit, title_contains, channel_name)


@plan_app.command("watch-later")
def plan_watch(ctx: typer.Context, all_items: Annotated[bool, typer.Option("--all")] = False,
               limit: Annotated[int | None, typer.Option("--limit")] = None,
               title_contains: Annotated[str | None, typer.Option("--title-contains")] = None,
               channel_name: Annotated[str | None, typer.Option("--channel-name")] = None) -> None:
    _plan_options(ctx, "watch-later", all_items, limit, title_contains, channel_name)


def _execute_loaded(runtime: Runtime, plan, *, yes: bool, phrase: str = "CLEAN") -> None:
    session, active = _authenticated_session(runtime)
    try:
        console.print(f"\nThis YouTube profile's library will be modified:\n  {active.channel_name}\n  {active.channel_handle}")
        console.print(f"Action: {plan.action}\nTargets: {len(plan.targets):,}")
        confirmed = yes or Prompt.ask(f"Type {phrase} to continue") == phrase
        if not confirmed:
            console.print("Cancelled. Zero changes made.")
            return
        with JobProgressDisplay() as progress:
            executor = Executor(
                session.page,
                runtime.settings,
                JobStore(runtime.paths.jobs),
                logger=configure_logging(runtime.paths.logs, debug=runtime.settings.debug),
                debug_dir=runtime.paths.debug if runtime.settings.debug else None,
                progress=progress.update,
            )
            job, summary = executor.execute_plan(plan, active, confirmed=True)
        _show_summary(job.job_id, summary)
    finally:
        _close(session)


@app.command("execute")
def execute_command(ctx: typer.Context, plan_id: str, yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    runtime = _runtime(ctx)
    plan = load_plan(runtime.paths.plans, plan_id)
    _execute_loaded(runtime, plan, yes=yes, phrase="DELETE PLAYLIST" if plan.action == "delete-playlist" else "CLEAN")


def _clean_area(ctx: typer.Context, area: str, all_items: bool, limit: int | None,
                title_contains: str | None, channel_name: str | None, execute: bool, yes: bool) -> None:
    runtime = _runtime(ctx)
    plan, path, result = _build_area_plan(runtime, area, all_items=all_items, limit=limit,
                                          title_contains=title_contains, channel_name=channel_name)
    console.print(
        f"Active YouTube profile: {result.account.channel_handle}\n"
        f"Discovered: {len(result.videos):,}\nSelected: {len(plan.targets):,}"
    )
    if not execute:
        console.print(f"\n[bold yellow]DRY RUN[/bold yellow]\nNo YouTube data has been modified.\nPlan saved: {path}")
        return
    _execute_loaded(runtime, plan, yes=yes)


def _clean_signature(ctx: typer.Context, area: str, all_items: bool, limit: int | None,
                     title_contains: str | None, channel_name: str | None, execute: bool, yes: bool):
    _clean_area(ctx, area, all_items, limit, title_contains, channel_name, execute, yes)


@likes_app.command("clean")
def likes_clean(ctx: typer.Context, all_items: Annotated[bool, typer.Option("--all")] = False,
                limit: Annotated[int | None, typer.Option("--limit")] = None,
                title_contains: Annotated[str | None, typer.Option("--title-contains")] = None,
                channel_name: Annotated[str | None, typer.Option("--channel-name")] = None,
                dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
                execute: Annotated[bool, typer.Option("--execute")] = False,
                yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    if dry_run and execute:
        raise typer.BadParameter("Choose either --dry-run or --execute")
    _clean_signature(ctx, "likes", all_items, limit, title_contains, channel_name, execute, yes)


@watch_app.command("clean")
def watch_clean(ctx: typer.Context, all_items: Annotated[bool, typer.Option("--all")] = False,
                limit: Annotated[int | None, typer.Option("--limit")] = None,
                title_contains: Annotated[str | None, typer.Option("--title-contains")] = None,
                channel_name: Annotated[str | None, typer.Option("--channel-name")] = None,
                dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
                execute: Annotated[bool, typer.Option("--execute")] = False,
                yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    if dry_run and execute:
        raise typer.BadParameter("Choose either --dry-run or --execute")
    _clean_signature(ctx, "watch-later", all_items, limit, title_contains, channel_name, execute, yes)


@playlists_app.command("list")
def playlists_list(ctx: typer.Context) -> None:
    result = _scan(_runtime(ctx), "playlists")
    show_playlists(result.playlists)
    show_scan(result)


def _choose_playlist(playlists: list[Playlist], playlist_id: str | None) -> Playlist:
    if playlist_id:
        match = next((item for item in playlists if item.playlist_id == playlist_id), None)
        if not match:
            raise typer.BadParameter(f"Playlist not found in rendered library: {playlist_id}")
        return match
    show_playlists(playlists)
    if not playlists:
        raise typer.BadParameter("No playlists discovered")
    selection = IntPrompt.ask("Select playlist", choices=[str(i) for i in range(1, len(playlists) + 1)])
    chosen = playlists[selection - 1]
    console.print(f"Selected: {chosen.title}\nURL: {chosen.playlist_url}")
    return chosen


def _playlist_write_block_reason(playlist: Playlist, account: AccountIdentity) -> str | None:
    """Explain playlist ownership cases that are definitely not editable.

    The playlist feed contains both playlists created by the active profile and
    playlists merely saved to its library.  Scanning either kind is valid, but
    YouTube only exposes per-item removal controls for the former.
    """
    if playlist.playlist_id.startswith("OLAK5uy_"):
        return "it is an auto-generated YouTube Music album playlist"

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


def _require_writable_playlist(playlist: Playlist, account: AccountIdentity) -> None:
    reason = _playlist_write_block_reason(playlist, account)
    if reason:
        raise typer.BadParameter(
            f"Playlist {playlist.title!r} cannot be cleaned item by item because {reason}. "
            "Only playlists created by the active YouTube profile are editable; saved albums, "
            "mixes, channel playlists, and other creators' playlists are read-only here."
        )


def _scan_selected_playlist(runtime: Runtime, playlist_id: str | None):
    index = _scan(runtime, "playlists")
    selected = _choose_playlist(index.playlists, playlist_id)
    session, account = _authenticated_session(runtime)
    try:
        result = Scanner(session.page, runtime.settings, account=account,
                         debug_dir=runtime.paths.debug if runtime.settings.debug else None).playlist(
                             selected.playlist_id, selected.title
                         )
        return selected, result
    finally:
        _close(session)


def _playlist_cleanup_targets(
    playlists: list[Playlist], account: AccountIdentity
) -> list[PlanTarget]:
    """Build one plan that safely handles both kinds of Library playlist."""
    targets: list[PlanTarget] = []
    for playlist in playlists:
        external = _playlist_write_block_reason(playlist, account) is not None
        cleanup_action = "remove-saved" if external else "delete-owned"
        targets.append(
            PlanTarget(
                target_id=f"playlist-clean:{playlist.playlist_id}",
                title=playlist.title,
                url=playlist.playlist_url,
                metadata={
                    "playlist_id": playlist.playlist_id,
                    "cleanup_action": cleanup_action,
                },
            )
        )
    return targets


def _saved_playlists(result: ScanResult) -> list[Playlist]:
    """Return playlists known to be saved rather than owned by this profile."""
    return [
        playlist
        for playlist in result.playlists
        if _playlist_write_block_reason(playlist, result.account) is not None
    ]


@playlists_app.command("show")
def playlists_show(ctx: typer.Context, playlist: Annotated[str | None, typer.Option("--playlist")] = None) -> None:
    selected, result = _scan_selected_playlist(_runtime(ctx), playlist)
    console.print(f"Playlist: {selected.title}\nURL: {selected.playlist_url}")
    show_videos(result)


@playlists_app.command("clean")
def playlists_clean(
    ctx: typer.Context,
    playlist: Annotated[str | None, typer.Option("--playlist", help="One playlist ID")] = None,
    all_items: Annotated[bool, typer.Option("--all", help="All owned and saved playlists")] = False,
    limit: Annotated[int | None, typer.Option("--limit", help="Clean at most N playlists")] = None,
    title_contains: Annotated[str | None, typer.Option("--title-contains", help="Filter playlist titles")] = None,
    channel_name: Annotated[str | None, typer.Option("--channel-name", help="Filter playlist owners")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    execute: Annotated[bool, typer.Option("--execute")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    confirm: Annotated[
        bool,
        typer.Option("--confirm", help="Require typing CLEAN before execution"),
    ] = False,
) -> None:
    """Delete owned playlists and remove saved playlists from the Library."""
    if dry_run and execute:
        raise typer.BadParameter("Choose either --dry-run or --execute")
    runtime = _runtime(ctx)
    result = _scan(runtime, "playlists")
    if playlist:
        selected_playlists = [_choose_playlist(result.playlists, playlist)]
    elif all_items or limit is not None or title_contains or channel_name:
        selected_playlists = result.playlists
    else:
        selected_playlists = [_choose_playlist(result.playlists, None)]
    if title_contains:
        needle = title_contains.casefold()
        selected_playlists = [item for item in selected_playlists if needle in item.title.casefold()]
    if channel_name:
        needle = channel_name.casefold()
        selected_playlists = [
            item for item in selected_playlists
            if item.owner_name and needle in item.owner_name.casefold()
        ]
    if limit is not None:
        if limit < 1:
            raise typer.BadParameter("limit must be at least 1")
        selected_playlists = selected_playlists[:limit]
    if not selected_playlists:
        raise typer.BadParameter("No playlists matched the selection.")
    snapshot = export_json(result, runtime.paths.backups, backup=True)
    targets = _playlist_cleanup_targets(selected_playlists, result.account)
    plan, path = create_plan(
        result.account,
        "clean-playlists",
        targets,
        runtime.browser_profile,
        runtime.paths.plans,
        source_snapshot=str(snapshot),
    )
    owned_count = sum(t.metadata["cleanup_action"] == "delete-owned" for t in plan.targets)
    saved_count = len(plan.targets) - owned_count
    console.print(
        f"Playlists selected: {len(plan.targets):,}\n"
        f"  Owned playlists to delete: {owned_count:,}\n"
        f"  Saved playlists to remove from Library: {saved_count:,}"
    )
    if owned_count:
        console.print("[bold red]Owned playlists will be permanently deleted.[/bold red]")
    if not execute:
        console.print(f"[bold yellow]DRY RUN[/bold yellow]\nNo YouTube data has been modified.\nPlan: {path}")
        return
    _execute_loaded(runtime, plan, yes=yes or not confirm)


@playlists_app.command("clear")
def playlists_clear(
    ctx: typer.Context,
    playlist: Annotated[str | None, typer.Option("--playlist")] = None,
    all_items: Annotated[bool, typer.Option("--all")] = False,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    title_contains: Annotated[str | None, typer.Option("--title-contains")] = None,
    channel_name: Annotated[str | None, typer.Option("--channel-name")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    execute: Annotated[bool, typer.Option("--execute")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """Remove video entries from one playlist owned by the active profile."""
    if dry_run and execute:
        raise typer.BadParameter("Choose either --dry-run or --execute")
    runtime = _runtime(ctx)
    selected, result = _scan_selected_playlist(runtime, playlist)
    _require_writable_playlist(selected, result.account)
    videos = select_videos(result.videos, all_items=all_items, limit=limit,
                           title_contains=title_contains, channel_name=channel_name)
    if not videos:
        raise typer.BadParameter("No targets selected. Use --all or a filter.")
    snapshot = export_json(result, runtime.paths.backups, backup=True)
    plan, path = create_plan(result.account, "remove-playlist-item", video_targets(videos),
                             runtime.browser_profile, runtime.paths.plans,
                             source_snapshot=str(snapshot))
    console.print(f"Playlist: {selected.title}\nSelected video entries: {len(plan.targets):,}")
    if not execute:
        console.print(f"[bold yellow]DRY RUN[/bold yellow]\nNo YouTube data has been modified.\nPlan: {path}")
        return
    _execute_loaded(runtime, plan, yes=yes)


@playlists_app.command("duplicates")
def playlists_duplicates(ctx: typer.Context, playlist: Annotated[str | None, typer.Option("--playlist")] = None) -> None:
    selected, result = _scan_selected_playlist(_runtime(ctx), playlist)
    selected.videos = result.videos
    _print_duplicates([selected])


def _print_duplicates(playlists: list[Playlist]) -> None:
    total = 0
    for playlist in playlists:
        duplicates = find_duplicates(playlist)
        for video_id, items in duplicates.items():
            total += len(items) - 1
            console.print(f"Playlist: {playlist.title}\n  Video: {items[0].title}\n  Video ID: {video_id}\n  Occurrences: {len(items)}")
    console.print(f"Duplicate entries removable: {total:,}")


@playlists_app.command("deduplicate")
def playlists_deduplicate(
    ctx: typer.Context,
    playlist: Annotated[str | None, typer.Option("--playlist")] = None,
    keep: Annotated[str, typer.Option("--keep")] = "first",
    execute: Annotated[bool, typer.Option("--execute")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    runtime = _runtime(ctx)
    selected, result = _scan_selected_playlist(runtime, playlist)
    _require_writable_playlist(selected, result.account)
    selected.videos = result.videos
    targets = dedup_targets(selected, keep=keep)
    if not targets:
        console.print("No duplicate entries found.")
        return
    console.print(f"Playlist: {selected.title}\nKeep: {keep}\nEntries selected for removal:")
    for target in targets:
        console.print(f"  {target.title} ({target.metadata['video_id']}) at original position {target.position}")
    snapshot = export_json(result, runtime.paths.backups, backup=True)
    plan, path = create_plan(result.account, "deduplicate-playlist", targets,
                             runtime.browser_profile, runtime.paths.plans,
                             source_snapshot=str(snapshot))
    if not execute:
        console.print(f"[bold yellow]DRY RUN[/bold yellow]\nNo YouTube data has been modified.\nPlan: {path}")
        return
    _execute_loaded(runtime, plan, yes=yes)


def _empty_playlists(runtime: Runtime) -> tuple[ScanResult, list[Playlist]]:
    result = _scan(runtime, "playlists", include_videos=True)
    # Only confidently empty when the detail scan completed and yielded zero rows.
    empty = [p for p in result.playlists if p.details_scanned and p.scan_status.value == "complete" and not p.videos]
    return result, empty


def _print_empty(playlists: list[Playlist]) -> None:
    empty = [p for p in playlists if p.details_scanned and p.scan_status.value == "complete" and not p.videos]
    console.print("Empty playlists found:")
    for playlist in empty:
        console.print(f"  {playlist.title} ({playlist.playlist_id})")
    console.print(f"{len(empty):,} empty playlists")


@playlists_app.command("empty")
def playlists_empty(ctx: typer.Context) -> None:
    _, empty = _empty_playlists(_runtime(ctx))
    _print_empty(empty)


def _playlist_delete_targets(playlists: list[Playlist]) -> list[PlanTarget]:
    return [PlanTarget(target_id=f"playlist:{p.playlist_id}", title=p.title, url=p.playlist_url,
                       metadata={"playlist_id": p.playlist_id}) for p in playlists]


@playlists_app.command("delete-empty")
def playlists_delete_empty(ctx: typer.Context, execute: Annotated[bool, typer.Option("--execute")] = False,
                           yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    runtime = _runtime(ctx)
    result, empty = _empty_playlists(runtime)
    _print_empty(empty)
    if not empty:
        return
    snapshot = export_json(result, runtime.paths.backups, backup=True)
    plan, path = create_plan(result.account, "delete-playlist", _playlist_delete_targets(empty),
                             runtime.browser_profile, runtime.paths.plans,
                             source_snapshot=str(snapshot))
    if not execute:
        console.print(f"[bold yellow]DRY RUN — no changes made.[/bold yellow]\nPlan: {path}")
        return
    # Broad deletion intentionally requires the strong phrase even with a plan.
    _execute_loaded(runtime, plan, yes=yes, phrase="DELETE PLAYLIST")


@playlists_app.command("delete")
def playlists_delete(ctx: typer.Context, playlist: Annotated[str | None, typer.Option("--playlist")] = None,
                     execute: Annotated[bool, typer.Option("--execute")] = False,
                     yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    runtime = _runtime(ctx)
    selected, result = _scan_selected_playlist(runtime, playlist)
    _require_writable_playlist(selected, result.account)
    snapshot = export_json(result, runtime.paths.backups, backup=True)
    plan, path = create_plan(result.account, "delete-playlist", _playlist_delete_targets([selected]),
                             runtime.browser_profile, runtime.paths.plans,
                             source_snapshot=str(snapshot))
    console.print(f"Playlist: {selected.title}\nURL: {selected.playlist_url}\nThis will delete the entire playlist.")
    if not execute:
        console.print(f"[bold yellow]DRY RUN — no changes made.[/bold yellow]\nPlan: {path}")
        return
    _execute_loaded(runtime, plan, yes=yes, phrase="DELETE PLAYLIST")


def _show_summary(job_id: str, summary) -> None:
    if summary.interrupted:
        console.print("[yellow]Cleanup stopped safely.[/yellow]")
    else:
        console.print("[green]Cleanup pass finished.[/green]")
    console.print(
        f"Completed: {summary.completed:,}\nFailed: {summary.failed:,}\n"
        f"Skipped: {summary.skipped:,}\nRemaining: {summary.remaining:,}"
    )
    if summary.remaining or summary.failed:
        console.print(f"Resume:\n  youtube-cleaner jobs resume {job_id}")


def _print_jobs(store: JobStore) -> None:
    jobs = store.list()
    if not jobs:
        console.print("No jobs found.")
    for job in jobs:
        console.print(f"{job.job_id}  {job.operation}  completed={len(job.completed)} failed={len(job.failed)} remaining={len(job.remaining)}")


@jobs_app.command("list")
def jobs_list(ctx: typer.Context) -> None:
    _print_jobs(JobStore(_runtime(ctx).paths.jobs))


@jobs_app.command("show")
def jobs_show(ctx: typer.Context, job_id: str) -> None:
    job = JobStore(_runtime(ctx).paths.jobs).load(job_id)
    console.print_json(job.model_dump_json(indent=2))


@jobs_app.command("resume")
def jobs_resume(
    ctx: typer.Context,
    job_id: str,
    limit: Annotated[int | None, typer.Option("--limit", min=1, help="Process at most N remaining targets")] = None,
) -> None:
    runtime = _runtime(ctx)
    store = JobStore(runtime.paths.jobs)
    job = store.load(job_id)
    session, active = _authenticated_session(runtime)
    try:
        with JobProgressDisplay() as progress:
            executor = Executor(
                session.page,
                runtime.settings,
                store,
                logger=configure_logging(runtime.paths.logs, debug=runtime.settings.debug),
                debug_dir=runtime.paths.debug if runtime.settings.debug else None,
                progress=progress.update,
            )
            summary = executor.resume(job, active, max_targets=limit)
        _show_summary(job.job_id, summary)
    finally:
        _close(session)


@debug_app.command("inspect")
def debug_inspect(ctx: typer.Context, area: Annotated[str, typer.Option("--area")] = "playlists") -> None:
    runtime = _runtime(ctx)
    if runtime.settings.headless:
        raise typer.BadParameter("debug inspect requires a visible browser")
    session, account = _authenticated_session(runtime)
    try:
        scanner = Scanner(session.page, runtime.settings, account=account, debug_dir=runtime.paths.debug)
        result = scanner.likes() if area == "likes" else scanner.watch_later() if area == "watch-later" else scanner.playlists()
        show_scan(result)
        path = capture_failure(session.page, runtime.paths.debug, f"inspect-{area}", [])
        console.print(f"Diagnostic screenshot: {path}\nURL: {session.page.url}")
        Prompt.ask("Inspect the browser and press Enter here to close", default="")
    finally:
        _close(session)


@debug_app.command("auth-status")
def debug_auth_status(ctx: typer.Context) -> None:
    """Inspect login indicators without entering the login flow."""
    runtime = _runtime(ctx)
    session = BrowserSession(runtime.paths, runtime.settings)
    try:
        session.__enter__()
    except BrowserProfileInUse as exc:
        raise typer.BadParameter(str(exc)) from None
    try:
        assert session.page is not None
        navigate(session.page, YOUTUBE_HOME)
        button = account_menu_button(session.page)
        sign_in = session.page.get_by_role("link", name="Sign in").count()
        console.print(f"URL: {session.page.url}")
        console.print(f"Visible account menu: {'yes' if button else 'no'}")
        console.print(f"Sign-in link candidates: {sign_in}")
        path = capture_failure(session.page, runtime.paths.debug, "auth-status", [])
        console.print(f"Screenshot: {path}")
    finally:
        _close(session)


@debug_app.command("inspect-menu")
def debug_inspect_menu(
    ctx: typer.Context,
    area: Annotated[str, typer.Option("--area", help="likes or watch-later")] = "watch-later",
) -> None:
    """Print first-row button semantics without clicking any menu action."""
    runtime = _runtime(ctx)
    session, account = _authenticated_session(runtime)
    try:
        assert session.page is not None
        url = WATCH_LATER if area == "watch-later" else LIKED_VIDEOS if area == "likes" else None
        if url is None:
            raise typer.BadParameter("area must be likes or watch-later")
        navigate(session.page, url)
        library = LibraryPage(session.page, debug_dir=runtime.paths.debug)
        containers = library.video_containers()
        if not containers:
            raise typer.BadParameter(f"No rendered rows found in {area}")
        details = containers[0].locator("button, [role='button'], yt-icon-button").evaluate_all(
            """nodes => nodes.map(node => ({
                tag: node.tagName.toLowerCase(),
                id: node.id || null,
                role: node.getAttribute('role'),
                aria_label: node.getAttribute('aria-label'),
                title: node.getAttribute('title'),
                text: (node.innerText || '').trim()
            }))""",
        )
        first_video = library.discover_videos()[0]
        library.open_video_menu(first_video.video_id)
        menu_locator = session.page.get_by_role("menuitem")
        menu_items = []
        for index in range(menu_locator.count()):
            item = menu_locator.nth(index)
            if item.is_visible():
                menu_items.append(
                    item.evaluate(
                        """node => ({
                            tag: node.tagName.toLowerCase(),
                            role: node.getAttribute('role'),
                            aria_label: node.getAttribute('aria-label'),
                            text: (node.innerText || '').trim()
                        })"""
                    )
                )
        library.close_menu()
        count_lines = [
            line.strip()
            for line in session.page.locator("body").inner_text().splitlines()
            if re.search(r"\d[\d.,]*\s+videos?", line, re.IGNORECASE)
        ][:10]
        console.print_json(
            data={
                "profile": account.channel_handle,
                "area": area,
                "buttons": details,
                "menu_items": menu_items,
                "visible_video_count_lines": count_lines,
            }
        )
        diagnostic = capture_failure(session.page, runtime.paths.debug, f"menu-inspect-{area}", [])
        console.print(f"Screenshot: {diagnostic}")
        console.print("No menu action was clicked; no YouTube data was modified.")
    finally:
        _close(session)


@debug_app.command("saved-playlist-controls")
def debug_saved_playlist_controls(
    ctx: typer.Context,
    playlist: Annotated[str | None, typer.Option("--playlist")] = None,
) -> None:
    """Inspect comma-separated playlist IDs without clicking any control."""
    runtime = _runtime(ctx)
    session, account = _authenticated_session(runtime)
    try:
        assert session.page is not None
        if playlist:
            playlist_ids = [value.strip() for value in playlist.split(",") if value.strip()]
        else:
            scanner = Scanner(session.page, runtime.settings, account=account, debug_dir=runtime.paths.debug)
            result = scanner.playlists()
            saved = _saved_playlists(result)
            if not saved:
                raise typer.BadParameter("No confidently identified saved playlists found")
            playlist_ids = [saved[0].playlist_id]
        observations = []
        for playlist_id in playlist_ids:
            navigate(session.page, f"https://www.youtube.com/playlist?list={playlist_id}")
            controls = session.page.locator("button, [role='button']").evaluate_all(
                """nodes => nodes.filter(node => {
                    const style = getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width && rect.height;
                }).map(node => ({
                    tag: node.tagName.toLowerCase(),
                    id: node.id || null,
                    class_name: node.className || null,
                    aria_label: node.getAttribute('aria-label'),
                    aria_pressed: node.getAttribute('aria-pressed'),
                    title: node.getAttribute('title'),
                    parent_tag: node.parentElement?.tagName?.toLowerCase() || null,
                    ancestor_tags: (() => {
                        const tags = [];
                        let current = node.parentElement;
                        for (let i = 0; current && i < 5; i++, current = current.parentElement) {
                            tags.push(current.tagName.toLowerCase());
                        }
                        return tags;
                    })(),
                    text: (node.innerText || '').trim()
                })).filter(item => {
                    const value = `${item.aria_label || ''} ${item.title || ''} ${item.text || ''}`;
                    return /more|action|menu|save|library|playlist|edit|share|delete|lainnya|tindakan|simpan|koleksi|ubah|bagikan|hapus/i.test(value);
                })"""
            )
            diagnostic = capture_failure(
                session.page,
                runtime.paths.debug,
                f"playlist-controls-{playlist_id}",
                [],
            )
            body_lines = [
                line.strip()
                for line in session.page.locator("body").inner_text().splitlines()
                if line.strip()
            ][:80]
            observations.append(
                {
                    "playlist_id": playlist_id,
                    "url": session.page.url,
                    "controls": controls,
                    "body_lines": body_lines,
                    "diagnostic": str(diagnostic),
                }
            )
        console.print_json(
            data={
                "profile": account.channel_handle,
                "playlists": observations,
            }
        )
        console.print("No control was clicked; no YouTube data was modified.")
    finally:
        _close(session)
