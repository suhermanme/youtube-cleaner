from typer.testing import CliRunner

from youtube_cleaner import cli
from youtube_cleaner.cli import app
from youtube_cleaner.models import AccountIdentity, CleanupPlan, ScanResult


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
