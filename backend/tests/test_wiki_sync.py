import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.wiki_manager import init_wiki
from services.wiki_sync import enqueue_wiki_sync, process_wiki_sync_job


def test_download_identity_sync_is_durable_and_does_not_enrich(tmp_path, monkeypatch):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    audio = music_dir / "Coldplay-Yellow-BV123.mp3"
    audio.write_bytes(b"audio")
    wiki_dir = tmp_path / "LLM-Wiki"
    init_wiki(str(wiki_dir))
    db_path = tmp_path / "wiki-sync.db"
    monkeypatch.setattr("services.wiki_ingest.settings.MUSIC_DIR", str(music_dir))

    job = enqueue_wiki_sync(
        {
            "bvid": "BV123",
            "title": "Yellow",
            "artist": "Coldplay",
            "video_title": "Coldplay - Yellow",
            "local_file_path": str(audio),
            "url": "https://www.bilibili.com/video/BV123",
        },
        db_path=db_path,
    )
    result = process_wiki_sync_job(
        job["id"], db_path=db_path, wiki_dir=str(wiki_dir)
    )

    assert result["status"] == "completed"
    assert (wiki_dir / "raw" / "songs" / "BV123.md").is_file()
    assert not list((wiki_dir / "wiki" / "entities" / "songs").glob("*.md"))
    assert result["result"]["enrichment_status"] == "pending"

    original = (wiki_dir / "raw" / "songs" / "BV123.md").read_text(encoding="utf-8")
    duplicate = process_wiki_sync_job(
        job["id"], db_path=db_path, wiki_dir=str(wiki_dir)
    )
    assert duplicate["status"] == "completed"
    assert duplicate["result"]["status"] == "source_already_registered"
    assert (wiki_dir / "raw" / "songs" / "BV123.md").read_text(encoding="utf-8") == original


def test_same_source_reuses_one_job(tmp_path):
    db_path = tmp_path / "wiki-sync.db"
    metadata = {"bvid": "BV123", "local_file_path": "x.mp3"}

    first = enqueue_wiki_sync(metadata, db_path=db_path)
    second = enqueue_wiki_sync(metadata | {"title": "updated"}, db_path=db_path)

    assert first["id"] == second["id"]
