from models import Track
from services import recommendation_service as service


def _track(track_id: str) -> Track:
    return Track(
        id=track_id,
        title=track_id,
        author="Artist",
        date="",
        filename=f"{track_id}.mp3",
        subDir="",
        size=1,
        url=f"/api/tracks/{track_id}.mp3",
    )


def test_radio_candidates_are_local_and_exclude_session_and_recent(monkeypatch):
    monkeypatch.setattr(
        service,
        "scan_tracks",
        lambda: [
            _track("current"),
            _track("recent"),
            _track("disliked"),
            _track("fresh-a"),
            _track("fresh-b"),
        ],
    )
    monkeypatch.setattr(
        service,
        "list_recent_tracks",
        lambda **kwargs: [{"track": {"id": "recent"}}],
    )
    monkeypatch.setattr(
        service,
        "list_feedback_excluded_track_ids",
        lambda user_id: {"disliked"},
    )

    result = service.recommend_local_radio_tracks(
        user_id="local",
        exclude_track_ids=["current"],
        limit=10,
    )

    assert {track["id"] for track in result["tracks"]} == {"fresh-a", "fresh-b"}
    assert result["reason_codes"] == [
        "local_only",
        "not_in_session",
        "not_recently_played",
        "negative_feedback_filtered",
    ]
