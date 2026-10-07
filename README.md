# YouTube Cleaner

YouTube Cleaner is a local, safety-first CLI for inspecting and cleaning a YouTube library by driving the normal, rendered YouTube website in a real browser. The browser is visible by default, every destructive run is tied to the currently active YouTube profile, and scanning/dry-run behavior is the default.

> Browser automation is inherently less stable than an official API. YouTube can change its UI at any time. This program stops on ambiguous selectors or unverifiable changes instead of guessing.

## What this program does

- Opens Playwright Chromium (or installed Chrome) with a dedicated persistent profile.
- Lets you complete Google sign-in, passkeys, 2FA, CAPTCHA, and verification manually.
- Detects the active YouTube profile/library—not merely the Google login. YouTube internally identifies every profile as a channel, even when it is used only for Likes, Watch Later, and saved playlists.
- Scans Liked videos, Watch Later, and playlists with bounded lazy loading.
- Exports JSON/CSV snapshots.
- Builds account-bound cleanup plans and resumable jobs.
- Removes Liked/Watch Later/playlist entries through visible menus.
- Finds duplicates, keeps the first or last occurrence, and can remove the rest.
- Finds confidently empty playlists and can delete selected playlists.

## What it does NOT use

**This application does not use the YouTube Data API.** It also does not use unofficial HTTP APIs, private endpoints, request interception, or direct backend requests. Therefore it requires:

- no Google Cloud project;
- no API key;
- no OAuth client ID or client secret;
- no YouTube API quota.

It never asks for, fills, records, or stores your Google password. It does not read Chrome's cookie database or copy cookies from your everyday browser profile.

## Installation

Requirements: Python 3.12+, [`uv`](https://docs.astral.sh/uv/), and a supported desktop environment.

```console
git clone git@github.com:suhermanme/youtube-cleaner.git
cd youtube-cleaner
uv sync
uv run playwright install chromium
```

Run either entry point:

```console
uv run youtube-cleaner
uv run python -m youtube_cleaner
```

Use installed Chrome with `--browser chrome`. Chromium is the default. Do not point this program at your everyday Chrome profile. The two browser engines use separate dedicated profiles so an accidental Chromium run cannot alter the native-Chrome login:

- installed Chrome: `~/.local/share/youtube-cleaner/browser-profile/`;
- bundled Playwright Chromium: `~/.local/share/youtube-cleaner/browser-profile-chromium/`.

Global options such as `--browser` and `--debug` must appear before the subcommand:

```console
uv run youtube-cleaner --browser chrome --debug scan likes
```

### Recommended macOS/installed Chrome workflow

Google may reject sign-in from Playwright's bundled browser. The most reliable flow tested on macOS is:

```console
# One-time manual login in normal installed Chrome
uv run youtube-cleaner browser login --native-chrome

# Read-only verification
uv run youtube-cleaner --browser chrome account
uv run youtube-cleaner --browser chrome scan likes
uv run youtube-cleaner --browser chrome scan watch-later
uv run youtube-cleaner --browser chrome scan playlists
```

When the native-login command asks you to quit Chrome, use **Command-Q** in the dedicated Chrome instance before pressing Enter in the terminal. Closing only the window leaves Chrome running and locks the profile.

## Authentication and first run

```console
uv run youtube-cleaner browser login
```

The visible browser opens YouTube. If signed out, the terminal prints:

```text
Please sign into the YouTube account you want to clean.
Complete Google authentication in the browser.
Return here when the YouTube home page is visible.
```

Complete account selection, password, passkey, 2FA, phone checks, or CAPTCHA in the browser itself. Initial login cannot run with `--headless`. The dedicated Playwright profile preserves the session between runs.

Google may refuse login when it detects that a browser is controlled by automation. This is a Google security decision and the application does not attempt to disguise or bypass Playwright. Use the native-Chrome handoff instead:

```console
uv run youtube-cleaner browser login --native-chrome
```

This opens installed Chrome normally, without Playwright control, but with YouTube Cleaner's dedicated profile. Sign in, select the intended YouTube profile, then completely quit that Chrome instance and return to the terminal. On macOS use **Command-Q**; closing the window with the red button leaves Chrome running and keeps the profile locked. The application reopens the same profile through Playwright and verifies the active profile. It does not read, copy, or export cookies.

Continue using the Chrome channel so the profile's encrypted session remains compatible:

```console
uv run youtube-cleaner --browser chrome --debug scan likes
```

For `--browser chrome`, the application launches installed Chrome itself with the dedicated profile and then attaches Playwright through Chrome's loopback DevTools connection. Chrome therefore opens and decrypts its own profile. YouTube Cleaner never exports or inspects cookies, and the debugging connection is not exposed beyond the local machine.

Check the active YouTube profile at any time:

```console
uv run youtube-cleaner account
```

A Google account can have multiple YouTube profiles (called channels by YouTube). Likes, Watch Later, and playlist state belong to the currently active YouTube profile, so this identity check is relevant even if you never upload videos. Switch profiles manually in YouTube, then rerun detection. If a stable profile handle/URL cannot be read from the visible account menu, cleanup is blocked.

Profile commands:

```console
uv run youtube-cleaner browser login
uv run youtube-cleaner browser logout
uv run youtube-cleaner browser reset
```

`logout` and `reset` delete only the selected browser's dedicated profile after the exact `RESET BROWSER` confirmation. Use `--browser chrome browser reset` for installed Chrome or `--browser chromium browser reset` for bundled Chromium. The application never inspects individual cookies.

## Proper end-to-end cleaning workflow

The following is the recommended workflow when using installed Chrome on macOS. Run commands from the project directory.

### 1. Install the project

```console
uv sync
uv run playwright install chromium
```

### 2. Authenticate manually in normal Chrome

```console
uv run youtube-cleaner browser login --native-chrome
```

Then:

1. Sign in through Google's normal UI in the Chrome window that opens.
2. Complete the password, passkey, 2FA, phone verification, or CAPTCHA in that browser—not in the terminal.
3. In YouTube, select the profile whose Likes, Watch Later, and playlists you intend to clean.
4. Completely quit that dedicated Chrome instance. On macOS use **Command-Q**; do not merely close its window.
5. Return to the terminal and press Enter only after Chrome has fully quit.

The application never asks for or records the Google password. Authentication remains in the dedicated installed-Chrome profile at `~/.local/share/youtube-cleaner/browser-profile/`.

### 3. Verify the active YouTube profile

```console
uv run youtube-cleaner --browser chrome account
```

Check the displayed profile name and handle carefully. Stop and switch profiles manually in YouTube if they are not the intended identity. All subsequent installed-Chrome commands must keep the global `--browser chrome` option before the subcommand.

### 4. Scan without changing anything

```console
uv run youtube-cleaner --browser chrome scan likes
uv run youtube-cleaner --browser chrome scan watch-later
uv run youtube-cleaner --browser chrome scan playlists
```

These commands are read-only. Do not proceed with cleanup if a scan says `possibly incomplete` until you understand its warning. For example, a difference between the page total and discovered Likes may represent unavailable or hidden entries.

To see playlist titles and IDs:

```console
uv run youtube-cleaner --browser chrome playlists list
```

### 5. Export the current state

```console
uv run youtube-cleaner --browser chrome export likes
uv run youtube-cleaner --browser chrome export watch-later
uv run youtube-cleaner --browser chrome export playlists
```

An export records the previous state but does not guarantee automatic restoration.

### 6. Perform a dry run

Choose the area you actually want to clean. A dry run creates a plan and performs zero destructive clicks:

```console
uv run youtube-cleaner --browser chrome likes clean --all --dry-run
uv run youtube-cleaner --browser chrome watch-later clean --all --dry-run
```

You can narrow the selection instead of using `--all`:

```console
uv run youtube-cleaner --browser chrome watch-later clean --limit 10 --dry-run
uv run youtube-cleaner --browser chrome likes clean --title-contains "kids" --dry-run
```

Review the active profile, discovered count, selected count, and saved plan before executing.

### 7. Execute a small canary first

Start with one item to verify that YouTube's current menu labels and removal behavior still match the automation:

```console
uv run youtube-cleaner --browser chrome watch-later clean --limit 1 --execute
```

Or, for Likes:

```console
uv run youtube-cleaner --browser chrome likes clean --limit 1 --execute
```

Confirm the active profile and type `CLEAN` when prompted. The application creates a backup and checkpointed job before changing anything.

### 8. Execute the intended cleanup

After the canary succeeds, rescan and run the desired cleanup:

```console
uv run youtube-cleaner --browser chrome watch-later clean --all --execute
uv run youtube-cleaner --browser chrome likes clean --all --execute
```

Run only the command for the area you intend to modify. `--yes` bypasses the interactive confirmation and should be used only when you have independently verified the active profile and plan.

During execution, the terminal displays live progress similar to:

```text
Cleanup ━━━━━━━━━━━━━━━╺━━━━━━━━━━━━━━ 121/250 0:04:18
removing: Removing from Watch Later and verifying the UI change · Video title… | completed=120 failed=1 skipped=0 remaining=130
```

The status changes while the application opens the page, searches lazy-loaded rows, removes an item, verifies the UI change, retries, checkpoints, or waits for pacing. `completed` increases only after the removal has been verified and the job checkpoint has been saved. A failed item remains in `remaining` so it can be retried with `jobs resume`.

Batch cleanup keeps the correct Likes, Watch Later, or selected playlist page open between adjacent targets. It navigates again only when the visible playlist URL changes. This avoids reloading YouTube for every deletion while preserving target lookup and post-action verification.

The default pacing delay is 1.5 seconds plus small jitter. After a successful one-item canary, advanced users can choose a shorter stability delay by placing the global option before the command:

```console
uv run youtube-cleaner --browser chrome --delay 0.75 watch-later clean --all --execute
```

Shorter delays increase the chance of stale menus, incomplete rerenders, or YouTube challenges. The delay exists for UI stability—not to evade detection—and verification/checkpointing are never skipped. If failures increase, return to the default `--delay 1.5`.

### 9. Check or resume the job

```console
uv run youtube-cleaner jobs list
uv run youtube-cleaner jobs show JOB_ID
uv run youtube-cleaner --browser chrome jobs resume JOB_ID --limit 1
uv run youtube-cleaner --browser chrome jobs resume JOB_ID
```

Ctrl+C stops safely after the current operation, checkpoints progress, and prints the resume command. Resume verifies that the active YouTube profile still matches the job.

### Cleaning an owned playlist

First list the playlist IDs, inspect the selected playlist, and dry-run the item removal:

```console
uv run youtube-cleaner --browser chrome playlists list
uv run youtube-cleaner --browser chrome playlists show --playlist PLAYLIST_ID
uv run youtube-cleaner --browser chrome playlists clean --playlist PLAYLIST_ID --all --dry-run
uv run youtube-cleaner --browser chrome playlists clean --playlist PLAYLIST_ID --limit 1 --execute
uv run youtube-cleaner --browser chrome playlists clean --playlist PLAYLIST_ID --all --execute
```

Only modify playlists owned by the active profile. Saved playlists belonging to other creators appear in the index, but removing/un-saving those playlists from the library is not currently implemented. Entire-playlist deletion is more destructive and requires the separate `playlists delete --playlist PLAYLIST_ID --execute` command plus the `DELETE PLAYLIST` confirmation phrase.

### When authentication expires

Do not enter credentials in a Playwright/CDP-controlled browser. Repeat the native authentication step:

```console
uv run youtube-cleaner browser login --native-chrome
```

Then verify with `uv run youtube-cleaner --browser chrome account` before resuming any job.

## Read-only scanning

Running without a subcommand shows an interactive menu and never immediately removes data.

```console
uv run youtube-cleaner scan
uv run youtube-cleaner scan likes
uv run youtube-cleaner scan watch-later
uv run youtube-cleaner scan playlists

uv run youtube-cleaner likes scan
uv run youtube-cleaner likes list
uv run youtube-cleaner watch-later scan
uv run youtube-cleaner watch-later list
uv run youtube-cleaner playlists list
uv run youtube-cleaner playlists show --playlist PLAYLIST_ID
```

The canonical area name is `playlists`; the singular alias `scan playlist` is also accepted. When using the native Chrome login profile, remember that `--browser chrome` is a global option and must come before `scan`:

```console
uv run youtube-cleaner --browser chrome scan playlists
```

Lists are scrolled until several passes find nothing new. A hard scroll cap prevents infinite loops. Output says `complete` or `possibly incomplete`; incomplete data is never presented as complete.

`scan playlists` inventories the playlist cards in the library index only. It does **not** open every playlist or scan every contained video. Use `playlists show`, `playlists duplicates`, or `playlists empty` when a deeper scan is intended.

The playlist index includes both:

- playlists created by the active YouTube profile; and
- playlists from other creators that were saved to the library.

Liked Videos (`LL`) and Watch Later (`WL`) are excluded from the playlist-index count because they have their own commands.

### Understanding scan counts

For Likes, Watch Later, and individual playlist pages, the scanner compares the rows it understands with an independent inventory of ordinary rendered links. When YouTube exposes a header total, output may look like:

```text
Videos discovered: 968
YouTube page total: 973
Unresolved/non-rendered entries: 5
Scan status: possibly incomplete
```

`Videos discovered` means rendered rows with a usable normal video URL. A difference from `YouTube page total` can represent private, deleted, unavailable, or otherwise hidden entries. The application does not invent IDs or metadata for those entries.

For the playlist index, the scanner independently compares extracted cards with all rendered playlist IDs. A future selector failure therefore reports `possibly incomplete` instead of incorrectly reporting `complete` with zero playlists. A `complete` result means bounded scrolling stabilized and the rendered integrity checks agreed; it is not a claim about hidden backend data.

## Exports and backups

```console
uv run youtube-cleaner export likes
uv run youtube-cleaner export watch-later --format csv
uv run youtube-cleaner export playlists
uv run youtube-cleaner likes export
uv run youtube-cleaner watch-later export --format csv
```

Exports go to `~/.local/share/youtube-cleaner/exports/`. Before destructive plans, JSON snapshots go to `~/.local/share/youtube-cleaner/backups/`.

**A backup is a record of the previous state. It does not guarantee automatic restoration**, because browser automation may not recreate every prior state perfectly.

## Plans, dry runs, and cleanup

Target selection and execution are separate. A plan stores the active YouTube profile identity and exact targets:

```console
uv run youtube-cleaner plan likes --all
uv run youtube-cleaner plan watch-later --limit 25 --channel-name "Example"
uv run youtube-cleaner execute PLAN_ID
```

Clean commands default to dry-run behavior even if `--dry-run` is omitted. Actual changes require `--execute`:

```console
uv run youtube-cleaner likes clean --all --dry-run
uv run youtube-cleaner likes clean --all --execute
uv run youtube-cleaner watch-later clean --title-contains "kids" --dry-run
uv run youtube-cleaner watch-later clean --all --execute
```

Supported selectors are `--all`, `--limit N`, `--title-contains TEXT`, and `--channel-name TEXT`. At least one must be supplied. `--yes` skips the interactive phrase only when explicitly provided. Without it, type `CLEAN`.

Before every execution the program re-detects the YouTube profile. If the plan belongs to `@example` and the browser is on `@different`, it refuses before any destructive click.

## Playlists

Commands accept `--playlist PLAYLIST_ID`; omit it for an interactive choice.

```console
uv run youtube-cleaner playlists list
uv run youtube-cleaner playlists show
uv run youtube-cleaner playlists clean --playlist PLAYLIST_ID --all --dry-run
uv run youtube-cleaner playlists clean --playlist PLAYLIST_ID --limit 10 --execute

uv run youtube-cleaner playlists duplicates --playlist PLAYLIST_ID
uv run youtube-cleaner playlists deduplicate --playlist PLAYLIST_ID --keep first
uv run youtube-cleaner playlists deduplicate --playlist PLAYLIST_ID --keep last --execute

uv run youtube-cleaner playlists empty
uv run youtube-cleaner playlists delete-empty
uv run youtube-cleaner playlists delete-empty --execute

uv run youtube-cleaner playlists delete --playlist PLAYLIST_ID
uv run youtube-cleaner playlists delete --playlist PLAYLIST_ID --execute
```

Whole-playlist deletion requires `--execute` and the phrase `DELETE PLAYLIST`. There is no broad `--all` playlist-delete option. Empty detection performs a detailed scan and only includes playlists whose detail scan completed and contained zero rendered video rows; on a large library this can take a long time.

`playlists delete`, playlist-item cleanup, and deduplication are intended for playlists owned by the active YouTube profile. Saved playlists owned by another creator also appear in the index, but YouTube does not expose their owner-only edit/delete actions. The current application does not implement “Remove saved playlist from library.” If the exact expected action is unavailable, execution stops safely rather than selecting a different menu action.

Deduplication compares ordinary rendered `/watch?v=VIDEO_ID` links. `--keep first` removes matching rows from the last edge; `--keep last` removes from the first edge. This avoids relying on stale absolute row indexes after rerenders.

## Jobs and resume

Execution creates a job under `~/.local/state/youtube-cleaner/jobs/`. It checkpoints atomically after every successful or failed target.

```console
uv run youtube-cleaner jobs list
uv run youtube-cleaner jobs show JOB_ID
uv run youtube-cleaner jobs resume JOB_ID
```

Resume rechecks the active YouTube profile against the job. Failed items remain recorded rather than silently marked complete. Ctrl+C stops new work, saves the current state, closes the browser cleanly, and prints the resume command.

For a small resume canary before continuing a large job:

```console
uv run youtube-cleaner --browser chrome jobs resume JOB_ID --limit 1
uv run youtube-cleaner --browser chrome jobs resume JOB_ID
```

## Safety mechanisms

- Visible browser by default; headless mode is advanced and forbidden for initial login.
- Read-only default and explicit plan/execution boundary.
- Stable handle/channel URL required for identity; display name alone is insufficient.
- Account comparison before any job is created or destructive UI action occurs.
- Exact English/Bahasa Indonesia destructive action names; no menu-position clicks.
- Target rows are re-queried after YouTube rerenders.
- Every destructive click must be followed by a verified disappearance/count change.
- Ambiguous actions, selector changes, CAPTCHA, unusual-traffic notices, and authentication challenges stop work.
- Conservative delay with small stability jitter (`--delay 1.5` by default); this is not bot-evasion.
- Bounded retries, scrolling caps, per-target checkpointing, and atomic state writes.
- Independent rendered-link/card checks prevent a broken selector from being reported as a confidently empty library.
- Logs exclude passwords, cookies, storage state, tokens, headers, and backend traffic.

## Diagnostics

```console
uv run youtube-cleaner --browser chrome --debug scan likes
uv run youtube-cleaner --browser chrome debug auth-status
uv run youtube-cleaner --browser chrome debug inspect --area playlists
uv run youtube-cleaner --browser chrome debug inspect-menu --area likes
uv run youtube-cleaner --browser chrome debug inspect-menu --area watch-later
```

`debug inspect` performs a read-only scan, saves diagnostics, and waits for Enter so you can inspect the browser. `debug inspect-menu` opens and closes the first row's visible menu to print its accessible labels, but it never selects an action.

With `--debug`, failures save a screenshot, rendered HTML, attempted selector labels, and page URL under `~/.local/state/youtube-cleaner/debug/`. Diagnostics do not intentionally include cookies, browser storage, request headers, or tokens. Rendered HTML and screenshots can contain visible private library content and may be very large after infinite scrolling; review them before sharing.

Selectors and translations are centralized in `src/youtube_cleaner/selectors.py`; UI interaction is encapsulated in `src/youtube_cleaner/pages.py`. After a YouTube layout change, use the diagnostic screenshot/HTML to update those modules. Never replace a broken semantic locator with an unverified positional selector.

## Tests

Automated tests use fake pages, fake adapters, temporary job stores, and pure URL/selection logic. They do not log into Google or touch a real account.

```console
uv run pytest
uv run pytest --cov=youtube_cleaner
```

Coverage includes legacy and modern YouTube card extraction, video/playlist ID extraction, false-zero integrity protection, lazy-load termination, filtering, serialization, job resume state, account mismatch, missing confirmation, zero-click dry-run architecture, English/Indonesian selector labels, retry logic, duplicate keep-first/keep-last, and Ctrl+C checkpointing.

For a manual read-only browser check, run `uv run youtube-cleaner scan likes`. For any manual destructive testing, first create a temporary YouTube playlist containing several disposable videos and operate only on that playlist.

## Troubleshooting

- **Google challenge/CAPTCHA/account verification:** complete it manually in the visible browser. The tool will not bypass it. Resume later if execution stopped.
- **“Browser or app may not be secure” / Google refuses Playwright login:** if native Chrome login has not been completed, run `youtube-cleaner browser login --native-chrome`, complete login, and quit Chrome before verification. Afterward, run normal commands with the global option before the subcommand, for example `youtube-cleaner --browser chrome scan playlists`. You do not need to log in again merely because `--browser chrome` was omitted once.
- **“Opening in existing browser session” / profile already in use:** the dedicated Chrome instance is still running. On macOS activate that window and press Command-Q, then retry. Do not delete Chromium's `SingletonLock` while its PID is alive.
- **Native Chrome was previously working but now asks for login:** rerun `youtube-cleaner browser login --native-chrome`, finish login, and completely quit that Chrome instance with Command-Q. Older releases shared one profile between Chrome and Chromium; current releases keep them separate to prevent recurrence.
- **Installed Chrome profile is not authenticated:** do not enter credentials in the controlled Chrome window. Run `youtube-cleaner browser login --native-chrome`, authenticate in the normal Chrome window, quit it completely, and then use `--browser chrome` for subsequent commands.
- **Expired session:** run `youtube-cleaner browser login` and authenticate manually.
- **Account mismatch:** switch to the YouTube profile shown in the plan/job, rerun `youtube-cleaner account`, then resume.
- **Selector failure/layout change:** rerun with `--debug`, inspect the artifacts, and update centralized selectors/page objects.
- **Profile already in use:** close the prior YouTube Cleaner browser. Only one process can use a persistent profile.
- **Profile corruption:** run `browser reset` after understanding that the dedicated login session will be removed.
- **Browser crash/interrupted job:** use `jobs list`, inspect the job, then `jobs resume JOB_ID`.
- **Scan finds zero despite visible items:** rerun with `--debug`. The scanner should report `possibly incomplete`, not `complete`; a complete zero without an explicit empty-state signal is a bug. The current selectors support both legacy playlist rows and modern `yt-lockup-view-model` cards.
- **Page total is larger than discovered videos:** the difference is reported as unresolved/non-rendered. It may contain private, deleted, unavailable, or UI-hidden entries. The tool will not guess their identity.
- **Incomplete lazy loading:** retry with a stable connection. The program reports `possibly incomplete` rather than assuming completion.
- **Installed Chrome incompatibility:** use bundled Chromium, which is Playwright's best-supported option.
- **Delete action missing:** the playlist may not be owned by the active YouTube profile, or YouTube changed its UI. The application stops rather than guessing.

## Current limitations

- YouTube DOM/custom-element names and accessible labels can change without notice.
- Only English and Bahasa Indonesia destructive labels are included.
- Counts are based solely on content exposed by the rendered UI and ordinary URLs.
- Unavailable/private entries can expose little metadata.
- Restoration is not automated.
- The playlist index includes saved playlists owned by other creators. Removing/un-saving those playlists from the library is not currently implemented.
- Owner-only playlist editing and deletion are intended for playlists created by the active YouTube profile; ambiguous ownership or missing actions stop execution.
- Real-account integration tests are intentionally not automated.
