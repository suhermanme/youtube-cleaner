import pytest

from youtube_cleaner.gui_commands import CleanupRequest, build_cleanup_args, build_resume_args
from youtube_cleaner.gui import clean_terminal_output


def test_build_unrestricted_continuous_cleanup() -> None:
    assert build_cleanup_args(
        CleanupRequest(area="playlists", execute=True, discover_more=True)
    ) == ["playlists", "clean", "--all", "--execute", "--discover-more"]


def test_build_filtered_dry_run() -> None:
    assert build_cleanup_args(
        CleanupRequest(
            area="likes",
            execute=False,
            all_items=False,
            limit=10,
            title_contains=" music ",
        )
    ) == [
        "likes",
        "clean",
        "--limit",
        "10",
        "--title-contains",
        "music",
        "--dry-run",
    ]


def test_cleanup_requires_a_selection() -> None:
    with pytest.raises(ValueError, match="Select all items"):
        build_cleanup_args(CleanupRequest(area="watch-later", execute=True, all_items=False))


def test_discover_more_rejects_limit_or_filter() -> None:
    with pytest.raises(ValueError, match="Continuous discovery"):
        build_cleanup_args(
            CleanupRequest(area="likes", execute=True, limit=1, discover_more=True)
        )


def test_build_resume_args() -> None:
    assert build_resume_args("remove-liked-123", limit=1) == [
        "jobs",
        "resume",
        "remove-liked-123",
        "--limit",
        "1",
    ]
    assert build_resume_args("remove-liked-123", discover_more=True)[-1] == "--discover-more"


def test_resume_rejects_limit_with_discovery() -> None:
    with pytest.raises(ValueError, match="either"):
        build_resume_args("job", limit=1, discover_more=True)


def test_terminal_output_removes_rich_control_codes() -> None:
    assert clean_terminal_output("\x1b[32mDone\x1b[0m\rNext") == "Done\nNext"
