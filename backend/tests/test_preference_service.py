from datetime import datetime, timedelta, timezone

from models import Track
from services import preference_service as service


def _track(track_id: str, author: str) -> Track:
    return Track(
        id=track_id,
        title=track_id,
        author=author,
        date="",
        filename=f"{track_id}.mp3",
        subDir="",
        size=1,
        url=f"/api/tracks/{track_id}.mp3",
    )


def test_recent_profile_combines_decayed_playback_and_explicit_feedback(monkeypatch):
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    occurred = (now - timedelta(days=1)).isoformat()
    monkeypatch.setattr(
        service,
        "list_playback_events_since",
        lambda **kwargs: [
            {
                "id": "play-1",
                "track_id": "liked-track",
                "title": "Liked",
                "author": "Preferred Artist",
                "event_type": "play_completed",
                "position_seconds": 180,
                "duration_seconds": 180,
                "scenario": "夜跑",
                "occurred_at": occurred,
            },
            {
                "id": "skip-1",
                "track_id": "skipped-track",
                "title": "Skipped",
                "author": "Skipped Artist",
                "event_type": "play_skipped",
                "position_seconds": 2,
                "duration_seconds": 180,
                "scenario": "夜跑",
                "occurred_at": occurred,
            },
        ],
    )
    monkeypatch.setattr(
        service,
        "list_track_feedback_events_since",
        lambda **kwargs: [
            {
                "id": "feedback-1",
                "track_id": "liked-track",
                "feedback_type": "like",
                "scenario": "夜跑",
                "occurred_at": occurred,
            }
        ],
    )
    monkeypatch.setattr(
        service,
        "scan_tracks",
        lambda: [
            _track("liked-track", "Preferred Artist"),
            _track("skipped-track", "Skipped Artist"),
        ],
    )

    profile = service.build_recent_preference_profile("local", now=now)
    seven_days = service.get_preference_window(profile, 7)

    assert profile["policy_version"] == "recent-preference-v1"
    assert seven_days["tracks"][0]["track_id"] == "liked-track"
    assert seven_days["tracks"][0]["score"] > 0
    skipped = next(item for item in seven_days["tracks"] if item["track_id"] == "skipped-track")
    assert skipped["score"] < 0
    assert seven_days["artists"][0]["author"] == "Preferred Artist"
    assert seven_days["scenarios"][0]["scenario"] == "夜跑"


def test_profile_excludes_evidence_outside_each_window(monkeypatch):
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    old = (now - timedelta(days=10)).isoformat()
    monkeypatch.setattr(
        service,
        "list_playback_events_since",
        lambda **kwargs: [
            {
                "track_id": "old-track",
                "title": "Old",
                "author": "Artist",
                "event_type": "play_completed",
                "position_seconds": 1,
                "duration_seconds": 1,
                "occurred_at": old,
            }
        ],
    )
    monkeypatch.setattr(service, "list_track_feedback_events_since", lambda **kwargs: [])
    monkeypatch.setattr(service, "scan_tracks", lambda: [_track("old-track", "Artist")])

    profile = service.build_recent_preference_profile("local", now=now)

    assert service.get_preference_window(profile, 7)["tracks"] == []
    assert service.get_preference_window(profile, 30)["tracks"][0]["track_id"] == "old-track"


def test_profile_can_be_scoped_to_current_scenario(monkeypatch):
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    occurred = (now - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        service,
        "list_playback_events_since",
        lambda **kwargs: [
            {
                "id": "drive-play",
                "track_id": "drive-track",
                "title": "Drive",
                "author": "Drive Artist",
                "event_type": "play_completed",
                "scenario": "开车",
                "occurred_at": occurred,
            },
            {
                "id": "sleep-play",
                "track_id": "sleep-track",
                "title": "Sleep",
                "author": "Sleep Artist",
                "event_type": "play_completed",
                "scenario": "睡前",
                "occurred_at": occurred,
            },
        ],
    )
    monkeypatch.setattr(service, "list_track_feedback_events_since", lambda **kwargs: [])
    monkeypatch.setattr(
        service,
        "scan_tracks",
        lambda: [
            _track("drive-track", "Drive Artist"),
            _track("sleep-track", "Sleep Artist"),
        ],
    )

    profile = service.build_recent_preference_profile(
        "local", now=now, scenario="开车"
    )
    tracks = service.get_preference_window(profile, 7)["tracks"]

    assert profile["scenario"] == "开车"
    assert [item["track_id"] for item in tracks] == ["drive-track"]


def test_unknown_artists_keep_track_evidence_without_artist_preference(monkeypatch):
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    authors = ["Unknown", "UNKNOWN ARTIST", "未知歌手", "N/A", "周杰伦"]
    catalog = [_track(str(i), author) for i, author in enumerate(authors)]
    monkeypatch.setattr(service, "list_playback_events_since", lambda **kwargs: [
        {"track_id": t.id, "title": t.title, "author": t.author, "event_type": "play_completed", "scenario": "跑步", "occurred_at": now.isoformat()} for t in catalog])
    monkeypatch.setattr(service, "list_track_feedback_events_since", lambda **kwargs: [])
    monkeypatch.setattr(service, "scan_tracks", lambda: catalog)
    window = service.get_preference_window(service.build_recent_preference_profile(now=now, scenario="跑步"), 7)
    assert len(window["tracks"]) == 5
    assert [item["author"] for item in window["artists"]] == ["周杰伦"]
    assert all(item["author"] == "" for item in window["tracks"] if item["track_id"] != "4")
