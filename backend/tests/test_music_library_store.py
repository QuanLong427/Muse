import pytest

from services import music_library_store as store


def _use_isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_DB_DIR", tmp_path)
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "music-library.db")


def _track(track_id: str = "library/song.mp3") -> dict:
    return {
        "id": track_id,
        "title": "Song",
        "author": "Artist",
        "date": "2026-10-01",
        "filename": "song.mp3",
        "subDir": "library",
        "size": 123,
        "url": "/api/tracks/library/song.mp3",
        "bvid": "BV123",
    }


def test_append_batch_is_atomic_owned_and_idempotent(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    playlist = store.create_playlist("Original")
    tracks = [{**_track("a"), "bvid": "BV-a"}, {**_track("b"), "bvid": "BV-b"}]
    result = store.append_playlist_batch(playlist["id"], tracks=tracks, batch_id="batch", expected_revision=0)
    assert result["added_count"] == 2
    assert result["revision"] == 1
    repeated = store.append_playlist_batch(playlist["id"], tracks=tracks, batch_id="batch", expected_revision=0)
    assert repeated["replayed"] is True
    assert repeated["revision"] == 1
    assert len(store.list_playlists()) == 1
    with pytest.raises(LookupError):
        store.append_playlist_batch(playlist["id"], tracks=tracks, batch_id="other", expected_revision=None, user_id="other-user")
    with pytest.raises(store.PlaylistRevisionConflictError):
        store.append_playlist_batch(playlist["id"], tracks=tracks, batch_id="other", expected_revision=0)


def test_append_batch_rolls_back_all_items_on_invalid_track(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    playlist = store.create_playlist("Original")
    with pytest.raises(ValueError):
        store.append_playlist_batch(playlist["id"], tracks=[_track("a"), {**_track("b"), "bvid": "new", "size": "invalid"}],
                                    batch_id="batch", expected_revision=0)
    assert store.get_playlist(playlist["id"])["items"] == []
    assert store.get_playlist(playlist["id"])["revision"] == 0


def test_named_playlist_lifecycle_is_separate_from_playback(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    playlist = store.create_playlist("Night Run")
    assert playlist["items"] == []

    playlist = store.add_playlist_track(
        playlist["id"],
        track=_track(),
        expected_revision=playlist["revision"],
    )
    assert playlist["revision"] == 1
    assert playlist["items"][0]["track"]["id"] == "library/song.mp3"

    duplicate = store.add_playlist_track(
        playlist["id"],
        track=_track(),
        expected_revision=playlist["revision"],
    )
    assert len(duplicate["items"]) == 1
    assert duplicate["revision"] == playlist["revision"]

    renamed = store.update_playlist(
        playlist["id"],
        name="Night Run 2",
        description="Fast songs",
        expected_revision=playlist["revision"],
    )
    assert renamed["name"] == "Night Run 2"
    assert renamed["revision"] == 2

    with pytest.raises(store.PlaylistRevisionConflictError):
        store.delete_playlist(playlist["id"], expected_revision=0)

    store.delete_playlist(playlist["id"], expected_revision=renamed["revision"])
    assert store.list_playlists() == []


def test_playlist_reorder_requires_exact_item_set(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    playlist = store.create_playlist("Order")
    playlist = store.add_playlist_track(
        playlist["id"], track=_track("a.mp3"), expected_revision=0
    )
    playlist = store.add_playlist_track(
        playlist["id"], track=_track("b.mp3"), expected_revision=1
    )
    ids = [item["id"] for item in playlist["items"]]

    reordered = store.reorder_playlist_items(
        playlist["id"], list(reversed(ids)), expected_revision=2
    )
    assert [item["id"] for item in reordered["items"]] == list(reversed(ids))

    with pytest.raises(ValueError):
        store.reorder_playlist_items(
            playlist["id"], [ids[0]], expected_revision=reordered["revision"]
        )


def test_recent_tracks_are_unique_and_based_on_playback_events(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    for event_type in ("play_started", "play_paused", "play_resumed"):
        store.record_playback_event(
            user_id="local",
            session_id="playback:local",
            item_id="item-1",
            event_type=event_type,
            track=_track(),
            position_seconds=12,
            duration_seconds=180,
            origin_type="manual",
            origin_id=None,
        )

    recent = store.list_recent_tracks()
    assert len(recent) == 1
    assert recent[0]["track"]["id"] == "library/song.mp3"
    assert recent[0]["last_event_type"] == "play_resumed"


def test_feedback_is_append_only_and_latest_negative_controls_exclusion(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)

    store.record_track_feedback(
        user_id="local",
        track_id="track-1",
        feedback_type="dislike",
    )
    assert store.list_feedback_excluded_track_ids("local") == {"track-1"}

    store.record_track_feedback(
        user_id="local",
        track_id="track-1",
        feedback_type="like",
    )
    assert store.list_feedback_excluded_track_ids("local") == set()
    summary = store.get_track_feedback_summary(user_id="local", track_id="track-1")
    assert summary[0]["latest_feedback"] == "like"
    assert summary[0]["counts"] == {"like": 1, "dislike": 1}


def test_recommendation_batch_is_immutable_and_user_scoped(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    batch = store.record_recommendation_batch(
        user_id="local",
        kind="radio",
        scenario="夜跑",
        current_track_id="current.mp3",
        profile_snapshot={"policy_version": "recent-preference-v1"},
        constraints={"limit": 1},
        items=[
            {
                "track": _track("recommended.mp3"),
                "score": 0.75,
                "reasons": [{"code": "recent_artist_affinity"}],
            }
        ],
    )

    assert batch["kind"] == "radio"
    assert batch["scenario"] == "夜跑"
    assert batch["items"][0]["track_id"] == "recommended.mp3"
    assert batch["items"][0]["reasons"] == [{"code": "recent_artist_affinity"}]
    assert store.get_recommendation_batch(batch["id"], user_id="someone-else") is None
