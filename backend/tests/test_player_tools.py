import json
import os
import sys

import pytest
from langchain_core.messages import ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ai_agent import _build_tools, _extract_client_action


@pytest.fixture(autouse=True)
def isolate_player_action_store(monkeypatch, tmp_path):
    from services import player_action_store

    monkeypatch.setattr(player_action_store, "_DB_DIR", tmp_path)
    monkeypatch.setattr(
        player_action_store, "_DB_PATH", tmp_path / "player-actions.db"
    )


def _tools_by_name(player_state=None):
    return {
        tool.name: tool
        for tool in _build_tools(player_state=player_state)
    }


def test_player_tools_are_available_and_return_snapshot():
    snapshot = {
        "available": True,
        "current": {"id": "track-1", "title": "测试歌曲"},
        "playing": True,
        "volume": 0.8,
    }
    tools = _tools_by_name(snapshot)

    assert "get_player_state" in tools
    assert "control_player" in tools
    assert "play_track" in tools
    assert "list_music_playlists" in tools
    assert "create_music_playlist" in tools
    assert "add_track_to_music_playlist" in tools
    assert "play_music_playlist" in tools
    assert "manage_music_playlist" in tools
    assert "manage_playback_session" in tools
    assert "set_playback_mode" in tools
    assert "record_track_feedback" in tools
    assert "get_recent_music_preferences" in tools
    assert "recommend_next" in tools
    assert "recommend_music" in tools
    assert "explain_recommendation" in tools
    assert "search_memory" in tools
    assert "remember_preference" in tools
    assert "forget_preference" in tools
    assert json.loads(tools["get_player_state"].invoke({})) == snapshot


def test_control_player_dispatches_typed_client_action():
    tools = _tools_by_name({"available": True})

    result = json.loads(
        tools["control_player"].invoke({"action": "set_volume", "value": 0.5})
    )

    assert result["status"] == "dispatched"
    assert result["action_id"] == result["client_action"]["action_id"]
    assert result["client_action"] | {"action_id": None} == {
        "target": "player",
        "action": "set_volume",
        "value": 0.5,
        "action_id": None,
    }


def test_control_player_rejects_invalid_value_and_missing_client():
    available = _tools_by_name({"available": True})["control_player"]
    unavailable = _tools_by_name()["control_player"]

    assert json.loads(
        available.invoke({"action": "set_volume", "value": 2})
    )["status"] == "invalid"
    assert json.loads(
        available.invoke({"action": "seek", "value": -1})
    )["status"] == "invalid"
    assert json.loads(unavailable.invoke({"action": "next"}))["status"] == "unavailable"


def test_extract_client_action_supports_langgraph_tool_message():
    output = ToolMessage(
        content=json.dumps(
            {
                "status": "dispatched",
                "client_action": {"target": "player", "action": "next"},
            }
        ),
        tool_call_id="tool-call-1",
    )

    assert _extract_client_action(output) == {
        "target": "player",
        "action": "next",
    }
    assert _extract_client_action("not-json") is None


def test_playback_session_and_mode_tools_dispatch_validated_actions(monkeypatch):
    from models import Track

    track = Track(
        id="library/song.mp3",
        title="Song",
        author="Artist",
        date="",
        filename="song.mp3",
        subDir="library",
        size=1,
        url="/api/tracks/library/song.mp3",
    )
    monkeypatch.setattr("services.music_manager.find_track_by_id", lambda track_id: track)
    tools = _tools_by_name(
        {
            "available": True,
            "items": [{"id": "item-1", "track": track.model_dump(mode="json")}],
        }
    )

    insert = json.loads(
        tools["manage_playback_session"].invoke(
            {"action": "insert_next", "track_id": track.id}
        )
    )
    assert insert["client_action"]["action"] == "insert_next"
    assert insert["client_action"]["track"]["id"] == track.id

    mode = json.loads(
        tools["set_playback_mode"].invoke(
            {"order_mode": "shuffle", "repeat_mode": "all"}
        )
    )
    assert mode["action_id"] == mode["client_action"].pop("action_id")
    assert mode["client_action"] == {
        "target": "player",
        "action": "set_playback_mode",
        "order_mode": "shuffle",
        "repeat_mode": "all",
    }

    invalid_reorder = json.loads(
        tools["manage_playback_session"].invoke(
            {"action": "reorder", "item_ids": []}
        )
    )
    assert invalid_reorder["status"] == "invalid"


def test_play_track_dispatches_exact_canonical_track(monkeypatch):
    from models import Track

    track = Track(
        id="20261001/周杰伦-最长的电影-BV1.mp3",
        title="最长的电影",
        author="周杰伦",
        date="",
        filename="周杰伦-最长的电影-BV1.mp3",
        subDir="20261001",
        size=1,
        url="/api/tracks/20261001/song.mp3",
        bvid="BV1",
    )
    monkeypatch.setattr("services.music_manager.find_track_by_id", lambda track_id: track)
    result = json.loads(
        _tools_by_name({"available": True})["play_track"].invoke({"track_id": track.id})
    )

    assert result["status"] == "dispatched"
    assert result["client_action"]["action"] == "play_track"
    assert result["client_action"]["track_id"] == track.id
    assert result["client_action"]["track"]["title"] == "最长的电影"


def test_manage_playlist_saves_current_session_as_named_playlist(monkeypatch, tmp_path):
    from models import Track
    from services import music_library_store as store

    track = Track(
        id="library/song.mp3",
        title="Song",
        author="Artist",
        date="",
        filename="song.mp3",
        subDir="library",
        size=1,
        url="/api/tracks/library/song.mp3",
    )
    monkeypatch.setattr(store, "_DB_DIR", tmp_path)
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "music-library.db")
    monkeypatch.setattr("services.music_manager.find_track_by_id", lambda track_id: track)
    tools = _tools_by_name(
        {
            "available": True,
            "items": [{"id": "item-1", "track": track.model_dump(mode="json")}],
        }
    )

    result = json.loads(
        tools["manage_music_playlist"].invoke(
            {"action": "save_current", "name": "当前播放"}
        )
    )

    assert result["status"] == "created"
    assert result["playlist"]["name"] == "当前播放"
    assert [item["track"]["id"] for item in result["playlist"]["items"]] == [
        track.id
    ]


def test_recommend_next_dispatches_audited_batch(monkeypatch):
    from models import Track

    track = Track(
        id="library/recommended.mp3",
        title="Recommended",
        author="Artist",
        date="",
        filename="recommended.mp3",
        subDir="library",
        size=1,
        url="/api/tracks/library/recommended.mp3",
    )
    monkeypatch.setattr(
        "services.recommendation_service.recommend_local_radio_tracks",
        lambda **kwargs: {
            "batch_id": "batch-1",
            "tracks": [track.model_dump(mode="json")],
            "recommendations": [],
            "reason_codes": ["recent_preference_ranked"],
        },
    )
    tools = _tools_by_name(
        {
            "available": True,
            "current": {"id": "current.mp3"},
            "items": [{"id": "item-1", "track": {"id": "current.mp3"}}],
        }
    )

    result = json.loads(tools["recommend_next"].invoke({"limit": 3}))

    assert result["status"] == "dispatched"
    assert result["batch_id"] == "batch-1"
    assert result["action_id"] == result["client_action"].pop("action_id")
    assert result["client_action"] == {
        "target": "player",
        "action": "add_tracks",
        "tracks": [track.model_dump(mode="json")],
        "origin_type": "radio",
        "origin_id": "batch-1",
    }


def test_recommend_music_registers_local_and_cloud_cards(monkeypatch):
    local_track = {
        "id": "library/local.mp3",
        "title": "Local Song",
        "author": "Local Artist",
        "date": "",
        "filename": "local.mp3",
        "subDir": "library",
        "size": 1,
        "url": "/api/tracks/library/local.mp3",
        "bvid": None,
    }
    video = {
        "bvid": "BV12345678",
        "title": "Cloud Song",
        "author": "Uploader",
        "duration": "4:00",
        "play": 100,
        "pic": "",
    }
    monkeypatch.setattr(
        "services.recommendation_service.recommend_conversational_tracks",
        lambda **kwargs: {
            "status": "ok",
            "track_ids": [local_track["id"], "bilibili:BV12345678"],
            "recommendations": [
                {"track": local_track, "score": 1, "reasons": []},
                {
                    "track": {"id": "bilibili:BV12345678"},
                    "score": 1,
                    "reasons": [],
                },
            ],
            "videos": [video],
        },
    )
    tools = _tools_by_name()
    activated = tools["activate_skill"].invoke({"name": "music-recommendation"})
    assert "Music Recommendation" in activated

    result = json.loads(tools["recommend_music"].invoke({"count": 2}))
    cards = json.loads(
        tools["present_tracks"].invoke({"track_ids": result["track_ids"]})
    )

    assert cards["status"] == "presented"
    assert [card["source_type"] for card in cards["tracks"]] == [
        "local",
        "bilibili",
    ]


def test_recommend_music_requires_progressive_skill_activation():
    tools = _tools_by_name()

    result = json.loads(tools["recommend_music"].invoke({}))

    assert result == {
        "status": "error",
        "error_code": "skill_activation_required",
        "required_skill": "music-recommendation",
        "retryable": True,
        "error": "请先加载 music-recommendation Skill，再重试推荐。",
    }


def test_recommend_music_requires_cloud_song_seeds_for_artist_scope():
    tools = _tools_by_name()
    tools["activate_skill"].invoke({"name": "music-recommendation"})

    result = json.loads(
        tools["recommend_music"].invoke(
            {"count": 4, "source_policy": "balanced", "artist": "周杰伦"}
        )
    )

    assert result["status"] == "error"
    assert result["error_code"] == "recommendation_seeds_required"
    assert result["retryable"] is True


def test_recommend_music_accepts_qwen_json_encoded_seed_string(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "services.recommendation_service.recommend_conversational_tracks",
        lambda **kwargs: captured.update(kwargs)
        or {"status": "empty", "track_ids": [], "recommendations": [], "videos": []},
    )
    tools = _tools_by_name()
    tools["activate_skill"].invoke({"name": "music-recommendation"})

    result = json.loads(
        tools["recommend_music"].invoke(
            {
                "artist": "周杰伦",
                "seed_songs": '["青花瓷", "稻香", "青花瓷"]',
            }
        )
    )

    assert result["status"] == "empty"
    assert captured["seed_songs"] == ["青花瓷", "稻香"]


def test_explain_recommendation_reads_persisted_evidence(monkeypatch):
    monkeypatch.setattr(
        "services.music_library_store.get_recommendation_batch",
        lambda batch_id, **kwargs: {
            "id": batch_id,
            "scenario": "夜跑",
            "created_at": "2026-10-02T00:00:00+00:00",
            "constraints": {"limit": 1},
            "items": [
                {
                    "track_id": "song.mp3",
                    "score": 0.8,
                    "reasons": [{"code": "recent_artist_affinity"}],
                }
            ],
        },
    )

    result = json.loads(
        _tools_by_name({"available": True})["explain_recommendation"].invoke(
            {"batch_id": "batch-1", "track_id": "song.mp3"}
        )
    )

    assert result["status"] == "ok"
    assert result["item"]["reasons"] == [{"code": "recent_artist_affinity"}]
