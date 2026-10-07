from models import Track
from services import smart_playlist_service as service


def _track(track_id: str, title: str, author: str) -> Track:
    return Track(
        id=track_id,
        title=title,
        author=author,
        date="",
        filename=f"{title}.mp3",
        subDir="library",
        size=1,
        url=f"/api/tracks/{track_id}",
    )


def _empty_profile():
    return {
        "generated_at": "2026-10-03T00:00:00+00:00",
        "policy_version": "recent-preference-v1",
        "windows": [{"days": 7, "tracks": [], "artists": [], "scenarios": []}],
    }


def _prepare(monkeypatch, tracks, memories=None):
    monkeypatch.setattr(service, "scan_tracks", lambda: tracks)
    monkeypatch.setattr(service, "list_recent_tracks", lambda **kwargs: [])
    monkeypatch.setattr(service, "list_feedback_excluded_track_ids", lambda user_id: set())
    monkeypatch.setattr(service, "list_memory_items", lambda *args, **kwargs: memories or [])
    monkeypatch.setattr(service, "_memory_preference_seeds", lambda *args: {
        "artists": [],
        "genres": [],
        "songs": [],
    })
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
        lambda *args, **kwargs: _empty_profile(),
    )
    recorded = []
    monkeypatch.setattr(
        service,
        "record_recommendation_batch",
        lambda **kwargs: recorded.append(kwargs) or {"id": "smart-batch"},
    )
    return recorded


def test_smart_cloud_search_ignores_unknown_and_balances_queries(monkeypatch):
    tracks = [_track("local-a", "本地甲", "Unknown"), _track("local-b", "本地乙", "Coldplay"), _track("local-c", "本地丙", "周杰伦")]
    _prepare(monkeypatch, tracks)
    profile = _empty_profile()
    profile["windows"][0]["artists"] = [{"author": "Unknown", "score": 999}, {"author": "Coldplay", "score": 3}, {"author": "周杰伦", "score": 2}]
    monkeypatch.setattr(service, "build_recent_preference_profile", lambda *args, **kwargs: profile)
    calls = []
    def search(query):
        calls.append(query)
        videos = [{"bvid": f"BV{query}{i}", "title": f"{query}《{query}曲{i}》MV", "author": "UP", "duration": "4:00", "play": 100-i} for i in range(5)]
        videos.insert(0, {"bvid": "BVnonmusic", "title": "ASMR 敲击音", "duration": "4:00", "play": 999999})
        return {"status": "ok", "videos": videos}
    result = service.generate_smart_playlist(user_id="u", count=6, cloud_search=search, duration_provider=lambda t: 180)
    assert "Unknown" not in calls
    assert calls[:2] == ["Coldplay", "周杰伦"]
    remote = result["remote_candidates"]
    assert len(remote) == 3
    assert remote[0]["author"] == "Coldplay"
    assert remote[1]["author"] == "周杰伦"
    assert all(item["bvid"] != "BVnonmusic" for item in remote)


def test_smart_playlist_applies_artist_version_and_memory_constraints(monkeypatch):
    tracks = [
        _track("jay-a.mp3", "晴天", "周杰伦"),
        _track("jay-live.mp3", "七里香 Live", "周杰伦"),
        _track("blocked.mp3", "夜曲", "不喜欢的歌手"),
        _track("other.mp3", "Yellow", "Coldplay"),
    ]
    memories = [
        {
            "kind": "avoidance",
            "memory_key": "artist:不喜欢的歌手",
            "directive": "不要推荐该歌手",
        }
    ]
    recorded = _prepare(monkeypatch, tracks, memories)

    result = service.generate_smart_playlist(
        user_id="local",
        scenario="夜跑",
        source_policy="local",
        count=5,
        include_artists=["周杰伦", "不喜欢的歌手"],
        exclude_versions=["live"],
        duration_provider=lambda track: 180,
    )

    assert [track["id"] for track in result["tracks"]] == ["jay-a.mp3"]
    assert result["status"] == "partial"
    assert result["batch_id"] == "smart-batch"
    assert recorded[0]["kind"] == "smart_playlist"
    assert recorded[0]["scenario"] == "夜跑"
    assert recorded[0]["constraints"]["exclude_versions"] == ["live"]


def test_smart_playlist_uses_duration_when_count_is_omitted(monkeypatch):
    tracks = [
        _track("a.mp3", "A", "Artist A"),
        _track("b.mp3", "B", "Artist B"),
        _track("c.mp3", "C", "Artist C"),
    ]
    _prepare(monkeypatch, tracks)

    result = service.generate_smart_playlist(
        user_id="local",
        duration_minutes=7,
        source_policy="local",
        duration_provider=lambda track: 240,
    )

    assert result["result_count"] == 2
    assert result["estimated_duration_seconds"] == 480
    assert result["duration_is_estimated"] is False


def test_smart_playlist_discloses_unverifiable_audio_constraints(monkeypatch):
    _prepare(monkeypatch, [_track("a.mp3", "A", "Artist")])

    result = service.generate_smart_playlist(
        user_id="local",
        count=1,
        source_policy="local",
        mood="专注",
        language="中文",
        energy_curve="rising",
        duration_provider=lambda track: None,
    )

    codes = {warning["code"] for warning in result["warnings"]}
    assert codes == {
        "mood_metadata_unavailable",
        "language_metadata_unavailable",
        "energy_curve_metadata_unavailable",
        "duration_estimated",
    }
    assert result["status"] == "partial"


def test_save_preview_persists_exact_canonical_tracks(monkeypatch, tmp_path):
    from services import music_library_store as store

    track = _track("library/a.mp3", "A", "Artist")
    monkeypatch.setattr(store, "_DB_DIR", tmp_path)
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "music-library.db")
    monkeypatch.setattr(
        service,
        "get_recommendation_batch",
        lambda *args, **kwargs: {
            "id": "smart-batch",
            "kind": "smart_playlist",
            "scenario": "夜跑",
            "items": [{"track_id": track.id}],
        },
    )
    monkeypatch.setattr(service, "find_track_by_id", lambda track_id: track)

    playlist = service.save_smart_playlist_preview(
        batch_id="smart-batch",
        name="夜跑 30 分钟",
        user_id="local",
    )

    assert playlist["name"] == "夜跑 30 分钟"
    assert playlist["revision"] == 1
    assert [item["track"]["id"] for item in playlist["items"]] == [track.id]


def _video(bvid, title):
    return {"bvid": bvid, "title": title, "author": "UP主", "duration": "4:00", "play": 100}


def test_append_preview_excludes_existing_tracks_and_binds_revision(monkeypatch):
    existing = _track("old", "晴天", "周杰伦")
    new = _track("new", "夜曲", "周杰伦")
    recorded = _prepare(monkeypatch, [existing, new])
    monkeypatch.setattr(service, "get_playlist", lambda *args: {"id": "p", "revision": 3, "items": [{"id": "existing-item", "track": existing.model_dump()}]})
    result = service.generate_smart_playlist(user_id="local", count=1, source_policy="local", target_playlist_id="p", duration_provider=lambda track: 240)
    assert [t["id"] for t in result["tracks"]] == [new.id]
    assert recorded[0]["constraints"]["target_revision"] == 3
    assert result["target_playlist_id"] == "p"


def test_append_mixed_preview_saves_to_original_playlist_without_duplicates(monkeypatch, tmp_path):
    from services import music_library_store as store, download_job_service as jobs
    import pytest
    monkeypatch.setattr(store, "_DB_DIR", tmp_path)
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "library.db")
    monkeypatch.setattr(jobs, "_DB_DIR", tmp_path)
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "downloads.db")
    playlist = store.create_playlist("歌单一")
    local = _track("new", "夜曲", "周杰伦")
    monkeypatch.setattr(service, "find_track_by_id", lambda identity: local)
    monkeypatch.setattr(service, "find_track_by_bvid", lambda bvid: None)
    monkeypatch.setattr(service, "get_recommendation_batch", lambda *args, **kwargs: {
        "kind": "smart_playlist", "constraints": {"target_playlist_id": playlist["id"], "target_revision": 0},
        "items": [{"track_id": local.id}, {"track_id": "bilibili:BV123", "track": {"bvid": "BV123", "title": "七里香"}}]})
    first = service.save_smart_playlist_preview(batch_id="batch", name="ignored", user_id="local", target_playlist_id=playlist["id"], schedule=False)
    second = service.save_smart_playlist_preview(batch_id="batch", name="ignored", user_id="local", target_playlist_id=playlist["id"], schedule=False)
    assert first["id"] == playlist["id"] == second["id"]
    assert first["name"] == "歌单一"
    assert first["added_count"] == 1
    assert first["download_job"]["id"] == second["download_job"]["id"]
    assert first["download_job"]["target_playlist_id"] == playlist["id"]
    assert len(store.list_playlists()) == 1
    with pytest.raises(ValueError):
        service.save_smart_playlist_preview(batch_id="batch", name="new", user_id="local", schedule=False)
    store.delete_playlist(playlist["id"], expected_revision=1)
    with pytest.raises(LookupError):
        service.save_smart_playlist_preview(batch_id="batch", name="歌单一", user_id="local", target_playlist_id=playlist["id"], schedule=False)
    assert not store.list_playlists()


def test_balanced_preview_contains_remote_candidates_without_fake_local_tracks(monkeypatch):
    recorded = _prepare(monkeypatch, [_track("local.mp3", "晴天", "周杰伦")])
    result = service.generate_smart_playlist(user_id="local", count=2, include_artists=["周杰伦"],
        cloud_search=lambda query: {"videos": [_video("BV123", "周杰伦《七里香》")]},
        duration_provider=lambda track: 200)
    assert result["source_plan"]["actual"] == {"local": 1, "cloud": 1}
    assert len(result["tracks"]) == 1
    assert result["remote_candidates"][0]["author"] == "周杰伦"
    assert result["remote_candidates"][0]["uploader"] == "UP主"
    assert recorded[0]["items"][1]["track"]["id"] == "bilibili:BV123"


def test_cloud_failure_reallocates_to_local_and_reports_failure(monkeypatch):
    _prepare(monkeypatch, [_track("a", "晴天", "周杰伦"), _track("b", "夜曲", "周杰伦")])
    result = service.generate_smart_playlist(user_id="local", count=2,
        cloud_search=lambda query: {"status": "error", "error": "412"}, duration_provider=lambda track: 200)
    assert result["source_plan"]["actual"] == {"local": 2, "cloud": 0}
    assert result["cloud_errors"]
    assert "cloud_search_failed" in {w["code"] for w in result["warnings"]}


def test_cloud_candidates_obey_avoidance_and_dedup(monkeypatch):
    _prepare(monkeypatch, [_track("local", "晴天", "周杰伦")],
        [{"kind": "avoidance", "memory_key": "song:夜曲"}])
    result = service.generate_smart_playlist(user_id="local", count=3,
        include_artists=["周杰伦"], exclude_versions=["live"],
        cloud_search=lambda query: {"videos": [
            _video("BV111", "周杰伦《晴天》"), _video("BV222", "周杰伦《夜曲》"),
            _video("BV333", "周杰伦《七里香》Live"), _video("BV444", "周杰伦《稻香》"),
            _video("BV555", "周杰伦《稻香》MV"),
        ]}, duration_provider=lambda track: 200)
    assert [t["bvid"] for t in result["remote_candidates"]] == ["BV444"]


def test_add_mixed_playlist_is_idempotent_and_queues_downloads(monkeypatch, tmp_path):
    from services import music_library_store as store, download_job_service as jobs
    monkeypatch.setattr(store, "_DB_DIR", tmp_path)
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "library.db")
    monkeypatch.setattr(jobs, "_DB_DIR", tmp_path)
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "downloads.db")
    local = _track("local", "晴天", "周杰伦")
    monkeypatch.setattr(service, "find_track_by_id", lambda identity: local)
    monkeypatch.setattr(service, "find_track_by_bvid", lambda bvid: None)
    monkeypatch.setattr(service, "get_recommendation_batch", lambda *args, **kwargs: {
        "kind": "smart_playlist", "scenario": "默认", "items": [
            {"track_id": local.id}, {"track_id": "bilibili:BV123", "track": {
                "bvid": "BV123", "title": "七里香", "author": "周杰伦", "uploader": "UP主"}}
        ]})
    first = service.save_smart_playlist_preview(batch_id="batch", name="混合歌单", user_id="local", schedule=False)
    second = service.save_smart_playlist_preview(batch_id="batch", name="混合歌单", user_id="local", schedule=False)
    assert first["id"] == second["id"]
    assert first["download_job"]["id"] == second["download_job"]["id"]
    assert [i["track"]["id"] for i in first["items"]] == [local.id]
    assert first["download_job"]["target_playlist_id"] == first["id"]


def test_cloud_only_preview_can_create_empty_playlist_pending_download(monkeypatch, tmp_path):
    from services import music_library_store as store, download_job_service as jobs
    monkeypatch.setattr(store, "_DB_DIR", tmp_path)
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "library.db")
    monkeypatch.setattr(jobs, "_DB_DIR", tmp_path)
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "downloads.db")
    monkeypatch.setattr(service, "find_track_by_bvid", lambda bvid: None)
    monkeypatch.setattr(service, "get_recommendation_batch", lambda *args, **kwargs: {
        "kind": "smart_playlist", "items": [{"track_id": "bilibili:BV123", "track": {"bvid": "BV123", "title": "Song"}}]})
    saved = service.save_smart_playlist_preview(batch_id="batch", name="在线歌单", user_id="local", schedule=False)
    assert saved["items"] == []
    assert saved["import_status"] == "queued"
