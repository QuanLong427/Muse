import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import services.wiki_manager as wiki_manager
from config import settings
from main import app
from services.wiki_manager import reset_wiki


client = TestClient(app)


def _make_existing_wiki(wiki_dir: Path) -> None:
    (wiki_dir / "raw" / "songs").mkdir(parents=True)
    (wiki_dir / "wiki" / "entities" / "songs").mkdir(parents=True)
    (wiki_dir / ".wiki-schema.md").write_text("- version: 2.0\n", encoding="utf-8")
    (wiki_dir / "raw" / "songs" / "BV1reset.md").write_text(
        """---
source_id: "bilibili:BV1reset"
bvid: "BV1reset"
linked_song: "测试歌曲"
local_relative_path: "album/歌手-测试歌曲-BV1reset.mp3"
audio_sha256: "known-hash"
---
""",
        encoding="utf-8",
    )
    (wiki_dir / "wiki" / "entities" / "songs" / "旧实体.md").write_text(
        "旧知识", encoding="utf-8"
    )


def test_reset_preserves_music_and_writes_recovery_manifest(tmp_path):
    wiki_dir = tmp_path / "LLM-Wiki"
    music_dir = tmp_path / "music"
    manifest_dir = tmp_path / "manifests"
    track_path = music_dir / "album" / "歌手-测试歌曲-BV1reset.mp3"
    track_path.parent.mkdir(parents=True)
    track_path.write_bytes(b"audio-bytes")
    _make_existing_wiki(wiki_dir)

    result = reset_wiki(str(wiki_dir), str(music_dir), str(manifest_dir))

    assert track_path.read_bytes() == b"audio-bytes"
    assert result["status"] == "reset"
    assert result["local_music_deleted"] is False
    assert result["preserved_local_tracks"] == 1
    assert result["previous_wiki_sources"] == 1
    assert result["schema_version"] == "3.0"
    assert not (wiki_dir / "wiki" / "entities" / "songs" / "旧实体.md").exists()

    manifest_path = next(manifest_dir.glob("wiki-reset-*.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["tracks"][0]["source_id"] == "bilibili:BV1reset"
    assert manifest["tracks"][0]["previous_linked_song"] == "测试歌曲"
    assert manifest["tracks"][0]["audio_sha256"] == "known-hash"
    assert str(tmp_path) not in manifest_path.read_text(encoding="utf-8")


def test_reset_hashes_local_tracks_without_bvid(tmp_path):
    wiki_dir = tmp_path / "LLM-Wiki"
    music_dir = tmp_path / "music"
    manifest_dir = tmp_path / "manifests"
    track_path = music_dir / "album" / "本地歌曲.mp3"
    track_path.parent.mkdir(parents=True)
    track_path.write_bytes(b"local-audio")

    result = reset_wiki(str(wiki_dir), str(music_dir), str(manifest_dir))
    manifest = json.loads(next(manifest_dir.glob("*.json")).read_text(encoding="utf-8"))

    assert result["preserved_local_tracks"] == 1
    assert manifest["tracks"][0]["source_id"].startswith("local:")
    assert len(manifest["tracks"][0]["audio_sha256"]) == 64


def test_reset_refuses_wiki_that_contains_music(tmp_path):
    wiki_dir = tmp_path / "unsafe"
    music_dir = wiki_dir / "music"
    music_dir.mkdir(parents=True)

    with pytest.raises(ValueError, match="包含本地音乐目录"):
        reset_wiki(str(wiki_dir), str(music_dir), str(tmp_path / "manifests"))


def test_reset_rolls_back_existing_wiki_when_initialization_fails(tmp_path, monkeypatch):
    wiki_dir = tmp_path / "LLM-Wiki"
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    _make_existing_wiki(wiki_dir)

    def fail_init(_wiki_dir):
        raise RuntimeError("init failed")

    monkeypatch.setattr(wiki_manager, "init_wiki", fail_init)

    with pytest.raises(RuntimeError, match="init failed"):
        reset_wiki(str(wiki_dir), str(music_dir), str(tmp_path / "manifests"))

    assert (wiki_dir / "wiki" / "entities" / "songs" / "旧实体.md").read_text(
        encoding="utf-8"
    ) == "旧知识"


def _configure_isolated_reset(tmp_path, monkeypatch):
    wiki_dir = tmp_path / "LLM-Wiki"
    music_dir = tmp_path / "music"
    manifest_dir = tmp_path / "manifests"
    track_path = music_dir / "album" / "歌手-测试歌曲-BV1reset.mp3"
    track_path.parent.mkdir(parents=True)
    track_path.write_bytes(b"api-audio-bytes")
    _make_existing_wiki(wiki_dir)
    monkeypatch.setattr(settings, "WIKI_DIR", str(wiki_dir))
    monkeypatch.setattr(settings, "MUSIC_DIR", str(music_dir))
    monkeypatch.setattr(wiki_manager, "WIKI_RESET_MANIFEST_DIR", manifest_dir)
    return wiki_dir, track_path, manifest_dir


def test_reset_api_uses_safe_reset_service(tmp_path, monkeypatch):
    wiki_dir, track_path, manifest_dir = _configure_isolated_reset(tmp_path, monkeypatch)

    response = client.post("/api/wiki/reset")

    assert response.status_code == 200
    assert response.json()["local_music_deleted"] is False
    assert response.json()["preserved_local_tracks"] == 1
    assert track_path.read_bytes() == b"api-audio-bytes"
    assert next(manifest_dir.glob("wiki-reset-*.json")).is_file()
    assert not (wiki_dir / "wiki/entities/songs/旧实体.md").exists()


def test_reset_wiki_chat_command_preserves_music(tmp_path, monkeypatch):
    wiki_dir, track_path, manifest_dir = _configure_isolated_reset(tmp_path, monkeypatch)
    monkeypatch.setattr("routers.chat.ensure_session", lambda *_args: "test-session")

    response = client.post("/api/chat", json={"message": "/reset-wiki"})

    assert response.status_code == 200
    assert "保留本地歌曲 1 首" in response.text
    assert track_path.read_bytes() == b"api-audio-bytes"
    assert next(manifest_dir.glob("wiki-reset-*.json")).is_file()
    assert not (wiki_dir / "wiki/entities/songs/旧实体.md").exists()
