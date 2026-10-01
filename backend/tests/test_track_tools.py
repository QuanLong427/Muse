import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import BiliVideo, Track
from services.ai_agent import _build_tools


def _tools():
    return {
        tool.name: tool
        for tool in _build_tools(player_state={"available": True})
    }


def test_present_tracks_accepts_only_ids_observed_this_turn(monkeypatch):
    local_track = Track(
        id="20261001/Coldplay-Yellow-BV1.mp3",
        title="Yellow",
        author="Coldplay",
        date="",
        filename="Coldplay-Yellow-BV1.mp3",
        subDir="20261001",
        size=1,
        url="/api/tracks/20261001/Yellow.mp3",
        bvid="BV1",
    )
    monkeypatch.setattr(
        "services.music_manager.search_tracks", lambda query, limit: [local_track]
    )
    tools = _tools()

    tools["local_search"].invoke({"query": "Yellow", "limit": 5})
    presented = json.loads(
        tools["present_tracks"].invoke({"track_ids": [local_track.id]})
    )
    rejected = json.loads(
        tools["present_tracks"].invoke({"track_ids": ["invented-track"]})
    )

    assert presented["status"] == "presented"
    assert presented["tracks"][0]["local_track"]["id"] == local_track.id
    assert rejected["status"] == "invalid"
    assert rejected["tracks"] == []


def test_convert_video_requires_structured_user_selection(monkeypatch):
    called = False

    def unexpected_download(**kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(
        "services.bili_downloader.download_bilibili_audio", unexpected_download
    )
    result = json.loads(
        _tools()["convert_video"].invoke(
            {
                "urls": ["https://www.bilibili.com/video/BV123"],
                "song_meta_json": '[{"bvid":"BV123","title":"七里香","artist":"周杰伦"}]',
            }
        )
    )

    assert result["status"] == "confirmation_required"
    assert result["errors"][0]["code"] == "download_confirmation_required"
    assert called is False


def test_bili_search_serializes_models_and_registers_presentable_ids(monkeypatch):
    async def fake_search(client, keyword):
        return {
            "total": 1,
            "videos": [
                BiliVideo(
                    bvid="BV123",
                    title="周杰伦《七里香》",
                    author="Uploader",
                    duration="4:59",
                    play=100,
                    pic="",
                )
            ],
        }

    monkeypatch.setattr("services.bili_client.search_videos", fake_search)
    tools = _tools()

    result = json.loads(tools["bili_search"].invoke({"keyword": "周杰伦 七里香"}))
    presented = json.loads(tools["present_tracks"].invoke({"track_ids": ["BV123"]}))

    assert result["videos"][0]["bvid"] == "BV123"
    assert presented["status"] == "presented"
    assert presented["tracks"][0]["track_id"] == "bilibili:BV123"
