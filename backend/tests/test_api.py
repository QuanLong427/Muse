import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from main import app
from config import settings

client = TestClient(app)


def test_health_endpoint():
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_search_missing_query():
    """Test search endpoint returns results even with empty query."""
    response = client.get("/api/search")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "tracks" in data


def test_bili_search_missing_keyword():
    """Test bili search endpoint rejects missing keyword."""
    response = client.get("/api/bili/search")
    assert response.status_code == 422  # FastAPI validation error


def test_bili_danmaku_missing_bvid():
    """Test bili danmaku endpoint rejects missing bvid."""
    response = client.get("/api/bili/danmaku")
    assert response.status_code == 422  # FastAPI validation error


def test_tracks_scan_missing_subdir():
    """Test tracks scan endpoint rejects missing subDir."""
    response = client.get("/api/tracks/scan")
    assert response.status_code == 422  # FastAPI validation error


def test_tracks_serve_path_traversal():
    """Test path traversal protection."""
    response = client.get("/api/tracks/../../etc/passwd")
    assert response.status_code in [403, 404]


def test_chat_missing_message():
    """Test chat endpoint rejects empty message."""
    response = client.post("/api/chat", json={"message": ""})
    assert response.status_code == 400


def test_player_action_ack_is_scoped_to_user_and_session(monkeypatch, tmp_path):
    from services import player_action_store as store

    monkeypatch.setattr(store, "_DB_DIR", tmp_path)
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "player-actions.db")
    issued = store.issue_player_action(
        user_id="local",
        session_id="default",
        payload={"target": "player", "action": "pause"},
    )

    response = client.post(
        f"/api/playback-session/actions/{issued['id']}/ack",
        json={
            "user_id": "local",
            "session_id": "default",
            "status": "succeeded",
            "result": {"observed": "paused"},
        },
    )
    wrong_user = client.post(
        f"/api/playback-session/actions/{issued['id']}/ack",
        json={
            "user_id": "someone-else",
            "session_id": "default",
            "status": "succeeded",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["result"] == {"observed": "paused"}
    assert wrong_user.status_code == 404


def test_config_update_does_not_persist_masked_api_key(monkeypatch):
    captured = {}

    def fake_update(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(settings, "update", fake_update)
    response = client.put(
        "/api/config",
        json={
            "base_url": "https://example.com/compatible-mode/v1/",
            "api_key": "sk-a****",
            "model_name": "qwen3.5-flash",
        },
    )

    assert response.status_code == 200
    assert captured == {
        "OPENAI_BASE_URL": "https://example.com/compatible-mode/v1",
        "MODEL_NAME": "qwen3.5-flash",
    }


def test_voice_transcribe_rejects_non_audio_body():
    response = client.post(
        "/api/voice/transcribe",
        content=b"not audio",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415


def test_voice_transcribe_returns_text(monkeypatch):
    async def fake_transcribe(audio, media_type):
        assert audio == b"recording"
        assert media_type == "audio/webm"
        return "下一首"

    monkeypatch.setattr("routers.voice.transcribe_audio", fake_transcribe)
    response = client.post(
        "/api/voice/transcribe",
        content=b"recording",
        headers={"Content-Type": "audio/webm;codecs=opus"},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "下一首"}


def test_radio_recommendation_forwards_scenario_and_current_track(monkeypatch):
    captured = {}

    def fake_recommend(**kwargs):
        captured.update(kwargs)
        return {"batch_id": "batch-1", "tracks": []}

    monkeypatch.setattr(
        "services.recommendation_service.recommend_local_radio_tracks",
        fake_recommend,
    )
    response = client.post(
        "/api/recommendations/radio",
        json={
            "user_id": "local",
            "exclude_track_ids": ["current.mp3"],
            "limit": 4,
            "scenario": "夜跑",
            "current_track_id": "current.mp3",
        },
    )

    assert response.status_code == 200
    assert captured == {
        "user_id": "local",
        "exclude_track_ids": ["current.mp3"],
        "limit": 4,
        "scenario": "夜跑",
        "current_track_id": "current.mp3",
    }


def test_recent_preferences_returns_requested_window(monkeypatch):
    monkeypatch.setattr(
        "services.preference_service.build_recent_preference_profile",
        lambda user_id: {
            "user_id": user_id,
            "generated_at": "2026-10-02T00:00:00+00:00",
            "policy_version": "recent-preference-v1",
            "windows": [
                {"days": 7, "tracks": []},
                {"days": 30, "tracks": [{"track_id": "song.mp3"}]},
            ],
        },
    )

    response = client.get("/api/preferences/recent?window_days=30")

    assert response.status_code == 200
    assert response.json()["window"] == {
        "days": 30,
        "tracks": [{"track_id": "song.mp3"}],
    }
