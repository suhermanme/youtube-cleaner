from youtube_cleaner.selectors import LABELS, label_pattern


def test_english_and_indonesian_selector_fallbacks():
    pattern = label_pattern("remove_from_liked")
    assert pattern.match("Remove from Liked videos")
    assert pattern.match("Hapus dari video yang disukai")
    assert not pattern.match("Delete playlist")


def test_destructive_labels_are_centralized():
    assert "delete_playlist" in LABELS
    assert all(len(values) >= 2 for values in LABELS.values())


def test_current_action_menu_labels_are_supported():
    pattern = label_pattern("more_actions")
    assert pattern.match("Action menu")
    assert pattern.match("Menu tindakan")
