import sqlite3

import pytest

from services import playback_session_store as store


def _use_isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_DB_DIR", tmp_path)
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "playlist.db")


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


def test_replaces_session_and_rejects_stale_revision(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    initial = store.get_playback_session()

    saved = store.replace_playback_session(
        items=[{"id": "item-1", "track": _track(), "origin_type": "agent"}],
        current_item_id="item-1",
        status="playing",
        order_mode="sequential",
        repeat_mode="off",
        progress_seconds=12.5,
        volume=0.6,
        history_item_ids=["item-1"],
        history_cursor=0,
        shuffle_bag_item_ids=[],
        expected_revision=initial["revision"],
    )

    assert saved["revision"] == initial["revision"] + 1
    assert saved["current_item_id"] == "item-1"
    assert saved["items"][0]["track"]["id"] == "library/song.mp3"
    assert saved["items"][0]["origin_type"] == "agent"
    assert saved["history_item_ids"] == ["item-1"]
    assert saved["history_cursor"] == 0

    with pytest.raises(store.RevisionConflictError):
        store.replace_playback_session(
            items=[],
            current_item_id=None,
            status="stopped",
            order_mode="sequential",
            repeat_mode="off",
            progress_seconds=0,
            volume=0.8,
            expected_revision=initial["revision"],
        )


def test_migrates_legacy_playlist_without_deleting_it(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    conn = sqlite3.connect(tmp_path / "playlist.db")
    conn.execute(
        """
        CREATE TABLE playlist (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT DEFAULT '',
            url TEXT DEFAULT '',
            filename TEXT DEFAULT '',
            bvid TEXT DEFAULT '',
            duration TEXT DEFAULT '',
            sub_dir TEXT DEFAULT '',
            position INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO playlist
            (id, title, author, url, filename, bvid, duration, sub_dir, position)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy/song.mp3",
            "Legacy Song",
            "Artist",
            "/api/tracks/legacy/song.mp3",
            "song.mp3",
            "BVLEGACY",
            "03:30",
            "legacy",
            0,
        ),
    )
    conn.commit()
    conn.close()

    snapshot = store.get_playback_session()

    assert snapshot["items"][0]["track"]["id"] == "legacy/song.mp3"
    assert snapshot["items"][0]["origin_type"] == "legacy"

    cleared = store.replace_playback_session(
        items=[],
        current_item_id=None,
        status="stopped",
        order_mode="sequential",
        repeat_mode="off",
        progress_seconds=0,
        volume=0.8,
        expected_revision=snapshot["revision"],
    )
    assert cleared["items"] == []
    assert store.get_playback_session()["items"] == []

    conn = sqlite3.connect(tmp_path / "playlist.db")
    assert conn.execute("SELECT COUNT(*) FROM playlist").fetchone()[0] == 1
    conn.close()


def test_invalid_current_item_is_cleared(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    snapshot = store.get_playback_session()

    saved = store.replace_playback_session(
        items=[{"id": "item-1", "track": _track()}],
        current_item_id="missing",
        status="playing",
        order_mode="sequential",
        repeat_mode="off",
        progress_seconds=42,
        volume=0.8,
        expected_revision=snapshot["revision"],
    )

    assert saved["current_item_id"] is None
    assert saved["status"] == "stopped"
    assert saved["progress_seconds"] == 0


def test_navigation_state_filters_unknown_and_duplicate_ids(monkeypatch, tmp_path):
    _use_isolated_db(monkeypatch, tmp_path)
    initial = store.get_playback_session()

    saved = store.replace_playback_session(
        items=[
            {"id": "item-1", "track": _track("one.mp3")},
            {"id": "item-2", "track": _track("two.mp3")},
        ],
        current_item_id="item-2",
        status="paused",
        order_mode="shuffle",
        repeat_mode="all",
        progress_seconds=8,
        volume=0.7,
        history_item_ids=["item-1", "missing", "item-2"],
        history_cursor=2,
        shuffle_bag_item_ids=["item-1", "item-1", "item-2", "missing"],
        expected_revision=initial["revision"],
    )

    assert saved["history_item_ids"] == ["item-1", "item-2"]
    assert saved["history_cursor"] == 1
    assert saved["shuffle_bag_item_ids"] == ["item-1"]
