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


def _empty_profile():
    return {
        "generated_at": "2026-10-02T00:00:00+00:00",
        "policy_version": "recent-preference-v1",
        "windows": [{"days": 7, "tracks": [], "artists": [], "scenarios": []}],
    }


def _prepare_conversation_dependencies(monkeypatch, tracks):
    monkeypatch.setattr(service, "scan_tracks", lambda: tracks)
    monkeypatch.setattr(service, "list_recent_tracks", lambda **kwargs: [])
    monkeypatch.setattr(service, "list_feedback_excluded_track_ids", lambda user_id: set())
    monkeypatch.setattr(service, "list_memory_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "_wiki_recommendation_context", lambda terms: {
        "status": "no_results",
        "artists": [],
        "genres": [],
        "songs": [],
        "evidence": [],
    })
    monkeypatch.setattr(
        service,
        "build_recent_preference_profile",
        lambda user_id, **kwargs: _empty_profile(),
    )
    recorded = []
    monkeypatch.setattr(
        service,
        "record_recommendation_batch",
        lambda **kwargs: recorded.append(kwargs) or {"id": "conversation-batch"},
    )
    return recorded


def _video(index: int, artist: str = "Cloud Artist") -> dict:
    return {
        "bvid": f"BV{index:08d}",
        "title": f"{artist} Cloud Song {index}",
        "author": f"Uploader {index}",
        "duration": "4:00",
        "play": 1000 - index,
        "pic": "",
    }


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
    monkeypatch.setattr(
        service,
        "build_recent_preference_profile",
        lambda user_id, **kwargs: _empty_profile(),
    )
    recorded = []
    monkeypatch.setattr(
        service,
        "record_recommendation_batch",
        lambda **kwargs: recorded.append(kwargs) or {"id": "batch-1"},
    )

    result = service.recommend_local_radio_tracks(
        user_id="local",
        exclude_track_ids=["current"],
        limit=10,
    )

    assert {track["id"] for track in result["tracks"]} == {"fresh-a", "fresh-b"}
    assert result["batch_id"] == "batch-1"
    assert result["reason_codes"] == [
        "local_only",
        "not_in_session",
        "not_recently_played",
        "negative_feedback_filtered",
        "recent_preference_ranked",
        "artist_diversity_applied",
    ]
    assert recorded[0]["kind"] == "radio"


def test_radio_ranks_recently_preferred_artist_first(monkeypatch):
    preferred = _track("preferred")
    preferred.author = "Preferred Artist"
    exploration = _track("exploration")
    exploration.author = "Other Artist"
    monkeypatch.setattr(service, "scan_tracks", lambda: [exploration, preferred])
    monkeypatch.setattr(service, "list_recent_tracks", lambda **kwargs: [])
    monkeypatch.setattr(service, "list_feedback_excluded_track_ids", lambda user_id: set())
    monkeypatch.setattr(
        service,
        "build_recent_preference_profile",
        lambda user_id, **kwargs: {
            "generated_at": "2026-10-02T00:00:00+00:00",
            "policy_version": "recent-preference-v1",
            "windows": [
                {
                    "days": 7,
                    "tracks": [],
                    "artists": [{"author": "Preferred Artist", "score": 5.0}],
                    "scenarios": [],
                }
            ],
        },
    )
    monkeypatch.setattr(
        service,
        "record_recommendation_batch",
        lambda **kwargs: {"id": "batch-2"},
    )

    result = service.recommend_local_radio_tracks(user_id="local", limit=2)

    assert result["tracks"][0]["id"] == "preferred"
    assert any(
        reason["code"] == "recent_artist_affinity"
        for reason in result["recommendations"][0]["reasons"]
    )


def test_conversation_recommendation_balances_local_and_cloud(monkeypatch):
    tracks = [_track(f"local-{index}") for index in range(4)]
    recorded = _prepare_conversation_dependencies(monkeypatch, tracks)

    result = service.recommend_conversational_tracks(
        user_id="local",
        count=4,
        cloud_search=lambda query: {
            "status": "ok",
            "attempts": 1,
            "videos": [_video(1), _video(2), _video(3)],
        },
    )

    assert result["status"] == "ok"
    assert result["source_plan"] == {
        "planned": {"local": 2, "cloud": 2},
        "actual": {"local": 2, "cloud": 2},
    }
    assert result["fallback_reasons"] == []
    assert len(result["track_ids"]) == 4
    assert recorded[0]["kind"] == "conversation"
    assert recorded[0]["constraints"]["source_policy"] == "balanced"


def test_conversation_recommendation_uses_cloud_when_local_is_insufficient(monkeypatch):
    matching = _track("local-jay")
    matching.author = "周杰伦"
    unrelated = _track("local-other")
    unrelated.author = "Other"
    _prepare_conversation_dependencies(monkeypatch, [matching, unrelated])

    result = service.recommend_conversational_tracks(
        user_id="local",
        artist="周杰伦",
        count=4,
        cloud_search=lambda query: {
            "status": "ok",
            "attempts": 1,
            "videos": [_video(1, "周杰伦"), _video(2, "周杰伦"), _video(3, "周杰伦")],
        },
    )

    assert result["source_plan"]["actual"] == {"local": 1, "cloud": 3}
    assert "local_candidates_insufficient" in result["fallback_reasons"]
    assert "本地候选不足" in result["user_notice"]


def test_conversation_recommendation_falls_back_to_local_on_cloud_failure(monkeypatch):
    tracks = [_track(f"local-{index}") for index in range(5)]
    _prepare_conversation_dependencies(monkeypatch, tracks)

    result = service.recommend_conversational_tracks(
        user_id="local",
        count=4,
        cloud_search=lambda query: {
            "status": "error",
            "error_code": "bilibili_transient_error",
            "error": "timeout",
            "retryable": True,
            "attempts": 2,
            "videos": [],
        },
    )

    assert result["status"] == "ok"
    assert result["source_plan"]["actual"] == {"local": 4, "cloud": 0}
    assert result["fallback_reasons"] == ["cloud_search_failed"]
    assert result["cloud_errors"][0]["attempts"] == 2
    assert "B站搜索暂时不可用" in result["user_notice"]


def test_conversation_local_policy_does_not_call_cloud(monkeypatch):
    tracks = [_track(f"local-{index}") for index in range(3)]
    _prepare_conversation_dependencies(monkeypatch, tracks)
    called = False

    def cloud_search(query):
        nonlocal called
        called = True
        return {"status": "ok", "videos": [_video(1)]}

    result = service.recommend_conversational_tracks(
        user_id="local",
        count=3,
        source_policy="local",
        cloud_search=cloud_search,
    )

    assert called is False
    assert result["source_plan"] == {
        "planned": {"local": 3, "cloud": 0},
        "actual": {"local": 3, "cloud": 0},
    }


def test_cloud_candidate_filter_rejects_unrequested_versions_and_indirect_credits():
    assert service._usable_cloud_video(_video(1, "周杰伦"), artist="周杰伦") is True
    assert service._usable_cloud_video(
        {**_video(2, "周杰伦"), "title": "AI 周杰伦演唱新歌"}, artist="周杰伦"
    ) is False
    assert service._usable_cloud_video(
        {**_video(3, "周杰伦"), "title": "曹杨新歌，周杰伦作曲"}, artist="周杰伦"
    ) is False
    assert service._usable_cloud_video(
        {**_video(4, "周杰伦"), "title": "周杰伦2026年最新歌曲试听"}, artist="周杰伦"
    ) is False


def test_song_identity_prefers_song_quotes_over_quality_badges():
    assert service._song_identity_from_title(
        "【4K60FPS】周杰伦《七里香》封神之作"
    ) == service._fold("七里香")
    assert service._song_identity_from_title(
        "【Hi-Res无损】｜《七里香》- 周杰伦"
    ) == service._fold("七里香")
    assert service._song_identity_from_title(
        "【4K Hi-Res】七里香-周杰伦", ["七里香"]
    ) == service._fold("七里香")


def test_conversation_recommendation_uses_song_seeds_and_deduplicates_cloud(monkeypatch):
    local = _track("local-qing-tian")
    local.title = "晴天"
    local.author = "周杰伦"
    recorded = _prepare_conversation_dependencies(monkeypatch, [local])
    queries = []

    def cloud_search(query):
        queries.append(query)
        if "七里香" in query:
            return {
                "status": "ok",
                "videos": [
                    {**_video(1, "周杰伦"), "title": "周杰伦《七里香》官方版"},
                    {**_video(2, "周杰伦"), "title": "周杰伦【七里香】高音质"},
                ],
            }
        if "稻香" in query:
            return {
                "status": "ok",
                "videos": [{**_video(3, "周杰伦"), "title": "周杰伦《稻香》MV"}],
            }
        return {
            "status": "ok",
            "videos": [{**_video(4, "周杰伦"), "title": "周杰伦《晴天》MV"}],
        }

    result = service.recommend_conversational_tracks(
        user_id="local",
        artist="周杰伦",
        count=3,
        seed_songs=["七里香", "稻香", "七里香"],
        cloud_search=cloud_search,
    )

    assert queries[:2] == ["周杰伦 七里香", "周杰伦 稻香"]
    assert result["source_plan"]["actual"] == {"local": 1, "cloud": 2}
    assert [video["title"] for video in result["videos"]] == [
        "周杰伦《七里香》官方版",
        "周杰伦《稻香》MV",
    ]
    assert recorded[0]["constraints"]["seed_songs"] == ["七里香", "稻香"]
