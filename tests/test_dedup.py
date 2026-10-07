from youtube_cleaner.models import LibraryVideo, Playlist
from youtube_cleaner.planner import dedup_targets, find_duplicates


def item(position, video_id="same"):
    return LibraryVideo(video_id=video_id, title="Repeated", video_url=f"/watch?v={video_id}",
                        playlist_name="P", playlist_url="https://youtube.com/playlist?list=P", position=position)


def playlist():
    return Playlist(playlist_id="P", title="P", playlist_url="https://youtube.com/playlist?list=P",
                    videos=[item(1), item(2), item(3), item(4, "unique")])


def test_duplicate_detection():
    assert len(find_duplicates(playlist())["same"]) == 3


def test_keep_first_removes_from_last_edge():
    targets = dedup_targets(playlist(), keep="first")
    assert len(targets) == 2
    assert all(t.metadata["occurrence"] == "last" for t in targets)
    assert [t.position for t in targets] == [2, 3]


def test_keep_last_removes_from_first_edge():
    targets = dedup_targets(playlist(), keep="last")
    assert len(targets) == 2
    assert all(t.metadata["occurrence"] == "first" for t in targets)
    assert [t.position for t in targets] == [1, 2]
