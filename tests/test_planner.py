from __future__ import annotations

from youtube_cleaner.models import AccountIdentity, LibraryVideo
from youtube_cleaner.planner import create_plan, load_plan, select_videos, video_targets


def video(video_id="v1", title="One", channel="Channel"):
    return LibraryVideo(video_id=video_id, title=title, video_url=f"https://youtube.com/watch?v={video_id}",
                        channel_name=channel)


def test_selection_requires_explicit_selector():
    videos = [video()]
    assert select_videos(videos) == []
    assert select_videos(videos, all_items=True) == videos


def test_selection_filters_and_limit():
    videos = [video("1", "Cats", "A"), video("2", "Dogs", "B"), video("3", "Cats 2", "A")]
    assert [v.video_id for v in select_videos(videos, title_contains="cats", limit=1)] == ["1"]
    assert [v.video_id for v in select_videos(videos, channel_name="b")] == ["2"]


def test_plan_serialization(tmp_path):
    account = AccountIdentity(authenticated=True, channel_name="Me", channel_handle="@me",
                              channel_url="https://youtube.com/@me")
    plan, path = create_plan(account, "remove-liked", video_targets([video()]), tmp_path / "profile", tmp_path)
    restored = load_plan(tmp_path, plan.plan_id)
    assert restored == plan
    assert path.exists()
