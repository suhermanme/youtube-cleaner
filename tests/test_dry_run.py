from typer.testing import CliRunner

from youtube_cleaner import cli
from youtube_cleaner.cli import app
from youtube_cleaner.models import AccountIdentity, CleanupPlan, Playlist, ScanResult


def test_help_does_not_launch_browser():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "YouTube" in result.stdout


def test_invalid_scan_area_is_rejected_before_browser_startup(monkeypatch):
    def forbidden_browser(*args, **kwargs):
        raise AssertionError("invalid scan area reached browser startup")

    monkeypatch.setattr(cli, "_authenticated_session", forbidden_browser)
    result = CliRunner().invoke(app, ["scan", "not-a-library-area"])
    assert result.exit_code != 0
    assert "unknown scan area" in result.stderr
    assert not isinstance(result.exception, AssertionError)


def test_playlist_scan_explicitly_reports_that_it_is_read_only(monkeypatch):
    account = AccountIdentity(
        authenticated=True,
        channel_name="Me",
        channel_handle="@me",
        channel_url="https://youtube.com/@me",
    )
    monkeypatch.setattr(
        cli,
        "_scan",
        lambda *args, **kwargs: ScanResult(area="playlists", account=account),
    )

    result = CliRunner().invoke(app, ["scan", "playlists"])

    assert result.exit_code == 0
    assert "Read-only scan complete" in result.stdout
    assert "No YouTube data was modified" in result.stdout
    assert "youtube-cleaner --browser chrome playlists" in result.stdout
    assert "clean --all --dry-run" in result.stdout


def test_clean_defaults_are_not_execute_flags():
    result = CliRunner().invoke(app, ["likes", "clean", "--help"])
    assert result.exit_code == 0
    assert "--execute" in result.stdout
    assert "--dry-run" in result.stdout

    playlist_result = CliRunner().invoke(app, ["playlists", "clean", "--help"])
    assert playlist_result.exit_code == 0
    assert "--execute" in playlist_result.stdout
    assert "--dry-run" in playlist_result.stdout


def test_dry_run_path_never_calls_executor(monkeypatch, tmp_path):
    destructive_calls = 0
    account = AccountIdentity(authenticated=True, channel_name="Me", channel_handle="@me",
                              channel_url="https://youtube.com/@me")
    plan = CleanupPlan(plan_id="dryrun", account=account, action="remove-liked", targets=[],
                       browser_profile=str(tmp_path / "profile"))
    result = ScanResult(area="likes", account=account)

    monkeypatch.setattr(cli, "_build_area_plan", lambda *args, **kwargs: (plan, tmp_path / "dryrun.json", result))

    def forbidden_execute(*args, **kwargs):
        nonlocal destructive_calls
        destructive_calls += 1
        raise AssertionError("dry-run entered execution")

    monkeypatch.setattr(cli, "_execute_loaded", forbidden_execute)
    outcome = CliRunner().invoke(app, ["likes", "clean", "--all", "--dry-run"])
    assert outcome.exit_code == 0
    assert "No YouTube data has been modified" in outcome.stdout
    assert destructive_calls == 0


def test_playlist_clean_all_selects_owned_and_saved_playlists_without_prompt(monkeypatch, tmp_path):
    account = AccountIdentity(
        authenticated=True,
        channel_name="Me",
        channel_handle="@me",
        channel_url="https://youtube.com/@me",
    )
    playlists = [
        Playlist(
            playlist_id="PLmine",
            title="Mine",
            playlist_url="https://youtube.com/playlist?list=PLmine",
            owner_name="Me",
            owner_url="https://youtube.com/@me",
        ),
        Playlist(
            playlist_id="OLAK5uy_album",
            title="Saved album",
            playlist_url="https://youtube.com/playlist?list=OLAK5uy_album",
        ),
        Playlist(
            playlist_id="PLexternal",
            title="Saved playlist",
            playlist_url="https://youtube.com/playlist?list=PLexternal",
            owner_name="Someone Else",
            owner_url="https://youtube.com/@someone-else",
        ),
    ]
    scan = ScanResult(area="playlists", account=account, playlists=playlists)
    monkeypatch.setattr(cli, "_scan", lambda *args, **kwargs: scan)
    monkeypatch.setattr(cli, "export_json", lambda *args, **kwargs: tmp_path / "backup.json")
    monkeypatch.setattr(
        cli,
        "_choose_playlist",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("--all prompted for one playlist")),
    )
    captured = {}

    def fake_create(account, action, targets, browser_profile, plans_dir, **kwargs):
        captured["action"] = action
        captured["targets"] = targets
        return (
            CleanupPlan(
                plan_id="saved",
                account=account,
                action=action,
                targets=targets,
                browser_profile=str(browser_profile),
            ),
            tmp_path / "saved.json",
        )

    monkeypatch.setattr(cli, "create_plan", fake_create)

    outcome = CliRunner().invoke(
        app,
        ["playlists", "clean", "--all", "--dry-run"],
    )

    assert outcome.exit_code == 0
    assert captured["action"] == "clean-playlists"
    assert [target.metadata["playlist_id"] for target in captured["targets"]] == [
        "PLmine",
        "OLAK5uy_album",
        "PLexternal",
    ]
    assert [target.metadata["cleanup_action"] for target in captured["targets"]] == [
        "delete-owned",
        "remove-saved",
        "remove-saved",
    ]
    assert "Playlists selected: 3" in outcome.stdout
    assert "Owned playlists to delete: 1" in outcome.stdout
    assert "Saved playlists to remove from Library: 2" in outcome.stdout
    assert "permanently deleted" in outcome.stdout

    confirmations = []
    monkeypatch.setattr(
        cli,
        "_execute_loaded",
        lambda runtime, plan, *, yes: confirmations.append(yes),
    )
    executed = CliRunner().invoke(app, ["playlists", "clean", "--all", "--execute"])
    confirmed = CliRunner().invoke(app, ["playlists", "clean", "--all", "--execute", "--confirm"])
    assert executed.exit_code == 0
    assert confirmed.exit_code == 0
    assert confirmations == [True, False]
