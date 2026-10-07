import json

from services import wiki_sync as sync, wiki_ingest as ingest
from services.wiki_manager import init_wiki, reset_wiki


def prepare(tmp_path, monkeypatch):
    audio = tmp_path / "music" / "song.mp3"
    audio.parent.mkdir()
    audio.write_bytes(b"audio")
    wiki = tmp_path / "wiki"
    init_wiki(str(wiki))
    db = tmp_path / "sync.db"
    monkeypatch.setattr(sync, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(ingest.settings, "MUSIC_DIR", str(audio.parent))
    return audio, wiki, db, {"bvid": "BV123", "title": "歌曲", "artist": "", "video_title": "某人现场翻唱《歌曲》", "local_file_path": str(audio)}


def analysis(uncertainties=None):
    return {"song": {"title": "歌曲", "overview": "原始来源中的歌曲", "confidence": 1,
        "evidence": [{"field": "title", "source": "raw.original_title", "value": "歌曲"}]},
        "artists": [], "albums": [], "genres": [], "connections": [], "uncertainties": uncertainties or []}


def test_download_enqueue_does_not_execute_wiki_io_or_model(tmp_path, monkeypatch):
    _, wiki, db, record = prepare(tmp_path, monkeypatch)
    def unexpected(*args, **kwargs):
        raise AssertionError("download completion must not enter the Wiki write lock")
    monkeypatch.setattr(sync, "process_wiki_sync_job", unexpected)
    monkeypatch.setattr(ingest, "_call_llm", unexpected)
    result = sync.sync_downloaded_sources([record], db_path=db, wiki_dir=str(wiki))
    assert result["status"] == "queued"
    assert result["jobs"][0]["enrichment_status"] == "queued"


def test_download_queues_and_builds_real_page_idempotently(tmp_path, monkeypatch):
    audio, wiki, db, record = prepare(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(ingest, "_call_llm", lambda prompt: calls.append(prompt) or json.dumps(analysis(), ensure_ascii=False))
    source = sync.sync_downloaded_sources([record], db_path=db, wiki_dir=str(wiki))["jobs"][0]
    assert source["status"] == "pending"
    assert not list((wiki / "raw/songs").glob("*.md"))
    assert source["enrichment_status"] == "queued"
    assert not list((wiki / "wiki/entities/songs").glob("*.md"))
    built = sync.process_next_wiki_enrichment(db_path=db)
    assert built["status"] == "completed"
    assert built["enrichment_status"] == "completed"
    page = wiki / "wiki/entities/songs/歌曲.md"
    assert page.is_file()
    assert 'verification_status: inferred' in page.read_text(encoding="utf-8")
    assert audio.read_bytes() == b"audio"
    duplicate = sync.sync_downloaded_sources([record], db_path=db, wiki_dir=str(wiki))["jobs"][0]
    assert duplicate["id"] == source["id"]
    assert duplicate["enrichment_status"] == "completed"
    assert sync.process_next_wiki_enrichment(db_path=db) is None
    assert len(calls) == 1


def test_incomplete_evidence_is_review_not_verified(tmp_path, monkeypatch):
    _, wiki, db, record = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(ingest, "_call_llm", lambda prompt: json.dumps(analysis(["歌手和版本待核实"]), ensure_ascii=False))
    sync.sync_downloaded_sources([record], db_path=db, wiki_dir=str(wiki))
    built = sync.process_next_wiki_enrichment(db_path=db)
    assert built["enrichment_status"] == "needs_review"
    assert not list((wiki / "wiki/entities/artists").glob("*.md"))
    # Cached recheck must retain review status rather than promote to success.
    sync.retry_wiki_enrichment(built["id"], db_path=db)
    assert sync.process_next_wiki_enrichment(db_path=db)["enrichment_status"] == "needs_review"


def test_failed_extraction_has_bounded_retry_and_preserves_audio(tmp_path, monkeypatch):
    audio, wiki, db, record = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(ingest, "_call_llm", lambda prompt: (_ for _ in ()).throw(RuntimeError("unavailable")))
    sync.sync_downloaded_sources([record], db_path=db, wiki_dir=str(wiki))
    for attempt in range(1, 4):
        with sync._connect(db) as connection:
            connection.execute("UPDATE wiki_sync_jobs SET next_attempt_at=0")
        result = sync.process_next_wiki_enrichment(db_path=db)
        assert result["enrichment_attempts"] == attempt
    assert result["enrichment_status"] == "failed"
    assert audio.is_file()
    assert sync.process_next_wiki_enrichment(db_path=db) is None
    retried = sync.retry_wiki_enrichment(result["id"], db_path=db)
    assert retried["enrichment_status"] == "queued"
    assert retried["enrichment_attempts"] == 0


def test_recovery_does_not_backfill_old_sources_and_reset_cancels_pending(tmp_path, monkeypatch):
    _, wiki, db, record = prepare(tmp_path, monkeypatch)
    old = sync.enqueue_wiki_sync(record, db_path=db)
    sync.process_wiki_sync_job(old["id"], db_path=db, wiki_dir=str(wiki))
    sync.recover_wiki_enrichment(db_path=db)
    assert sync.get_wiki_sync_job(old["id"], db_path=db)["enrichment_status"] == "not_requested"
    sync.sync_downloaded_sources([record], db_path=db, wiki_dir=str(wiki))
    with sync._connect(db) as connection:
        connection.execute("UPDATE wiki_sync_jobs SET enrichment_status='running'")
    sync.recover_wiki_enrichment(db_path=db)
    assert sync.get_wiki_sync_job(old["id"], db_path=db)["enrichment_status"] == "queued"
    reset_wiki(str(wiki), music_dir=str(tmp_path / "music"), manifest_dir=str(tmp_path / "manifests"))
    assert sync.get_wiki_sync_job(old["id"], db_path=db)["enrichment_status"] == "cancelled"
    assert sync.process_next_wiki_enrichment(db_path=db) is None
