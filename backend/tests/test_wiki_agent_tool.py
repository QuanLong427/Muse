import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import ai_agent
from services.ai_agent import _build_tools


def _wiki_tool():
    return {tool.name: tool for tool in _build_tools()}["wiki_ingest"]


def test_wiki_ingest_plan_is_read_only(monkeypatch):
    called = False

    def unexpected_ingest(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("services.wiki_ingest.ingest_song", unexpected_ingest)

    result = json.loads(
        _wiki_tool().invoke({
            "song_meta_json": json.dumps({"bvid": "BV123", "title": "测试歌曲"}),
            "apply": False,
        })
    )

    assert result["status"] == "planned"
    assert result["writes_performed"] is False
    assert called is False


def test_wiki_ingest_apply_uses_backend_service(monkeypatch):
    ingested = []

    monkeypatch.setattr(
        "services.wiki_manager.get_wiki_status",
        lambda wiki_dir=None: {"initialized": True},
    )
    monkeypatch.setattr(
        "services.wiki_ingest.ingest_song",
        lambda metadata, wiki_dir=None: ingested.append(metadata) or {"status": "created", "title": metadata["title"]},
    )

    result = json.loads(
        _wiki_tool().invoke({
            "song_meta_json": json.dumps({
                "bvid": "BV123",
                "title": "测试歌曲",
                "videoTitle": "原始视频标题",
            }),
            "apply": True,
        })
    )

    assert result["status"] == "completed"
    assert result["writes_performed"] is True
    assert ingested[0]["video_title"] == "原始视频标题"


def test_wiki_ingest_rejects_missing_source_identity():
    result = json.loads(
        _wiki_tool().invoke({"song_meta_json": "{}", "apply": False})
    )

    assert result["status"] == "invalid"


def test_convert_video_returns_ingest_ready_metadata_without_writing_wiki(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent.settings, "MUSIC_DIR", str(tmp_path))
    monkeypatch.setattr(ai_agent, "_find_bash", lambda: "bash")
    monkeypatch.setattr(ai_agent, "_to_unix_path", lambda path: path)

    ingest_called = False

    def unexpected_ingest(*args, **kwargs):
        nonlocal ingest_called
        ingest_called = True

    def fake_run(*args, **kwargs):
        target_dir = next(tmp_path.iterdir())
        (target_dir / "download-BV123.mp3").write_bytes(b"test audio")
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr("services.wiki_ingest.ingest_song", unexpected_ingest)
    monkeypatch.setattr(ai_agent.subprocess, "run", fake_run)

    result = json.loads(
        {tool.name: tool for tool in _build_tools()}["convert_video"].invoke({
            "urls": ["https://www.bilibili.com/video/BV123"],
            "song_meta_json": json.dumps([{
                "bvid": "BV123",
                "title": "测试歌曲",
                "artist": "测试歌手",
                "uploader": "测试上传者",
                "videoTitle": "原始视频标题",
            }]),
        })
    )

    assert result["success"] is True
    assert ingest_called is False
    assert result["files"][0] == {
        "original": "download-BV123.mp3",
        "renamed": "测试歌手-测试歌曲-BV123.mp3",
        "bvid": "BV123",
        "title": "测试歌曲",
        "artist": "测试歌手",
        "uploader": "测试上传者",
        "video_title": "原始视频标题",
        "url": "https://www.bilibili.com/video/BV123",
        "local_file_path": str(
            next(tmp_path.iterdir()) / "测试歌手-测试歌曲-BV123.mp3"
        ),
    }
