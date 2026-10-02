import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import download_job_service


def _isolated_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(download_job_service, "_DB_DIR", tmp_path)
    monkeypatch.setattr(download_job_service, "_DB_PATH", tmp_path / "jobs.db")
    download_job_service.init_download_job_db()


def _item(bvid="BV123"):
    return {
        "bvid": bvid,
        "url": f"https://www.bilibili.com/video/{bvid}",
        "title": "七里香",
        "artist": "周杰伦",
    }


def test_download_job_persists_success_and_wiki_sync(monkeypatch, tmp_path):
    _isolated_jobs(monkeypatch, tmp_path)
    job = download_job_service.create_download_job(
        user_id="user-a", items=[_item()], schedule=False
    )
    wiki_sources = []

    def fake_download(**kwargs):
        kwargs["progress_callback"](
            {"bvid": "BV123", "status": "downloading", "progress": 42}
        )
        return {
            "success": True,
            "status": "success",
            "files": [{"bvid": "BV123", "local_file_path": "song.mp3"}],
            "errors": [],
        }

    monkeypatch.setattr(download_job_service, "download_bilibili_audio", fake_download)
    monkeypatch.setattr("services.music_manager.find_track_by_bvid", lambda bvid: None)
    monkeypatch.setattr(
        "services.wiki_sync.sync_downloaded_sources",
        lambda sources: wiki_sources.extend(sources) or {"status": "completed"},
    )

    result = download_job_service.run_download_job(job["id"])

    assert result["status"] == "completed"
    assert result["progress"] == 100
    assert result["items"][0]["status"] == "downloaded"
    assert result["result"]["wiki_sync"]["status"] == "completed"
    assert wiki_sources == [{"bvid": "BV123", "local_file_path": "song.mp3"}]


def test_global_downloader_failure_marks_every_item_failed(monkeypatch, tmp_path):
    _isolated_jobs(monkeypatch, tmp_path)
    job = download_job_service.create_download_job(
        user_id="user-a",
        items=[_item("BV123"), _item("BV456")],
        schedule=False,
    )
    monkeypatch.setattr(
        download_job_service,
        "download_bilibili_audio",
        lambda **kwargs: {
            "success": False,
            "status": "failed",
            "files": [],
            "errors": [{"code": "music_dir_missing", "message": "missing"}],
        },
    )
    monkeypatch.setattr("services.music_manager.find_track_by_bvid", lambda bvid: None)

    result = download_job_service.run_download_job(job["id"])

    assert result["status"] == "failed"
    assert result["failed_items"] == 2
    assert {item["status"] for item in result["items"]} == {"failed"}
    assert all(
        item["error"]["code"] == "music_dir_missing" for item in result["items"]
    )


def test_cancel_queued_job_and_retry_failed_items(monkeypatch, tmp_path):
    _isolated_jobs(monkeypatch, tmp_path)
    job = download_job_service.create_download_job(
        user_id="user-a", items=[_item()], schedule=False
    )

    cancelled = download_job_service.request_download_job_cancel(
        job["id"], user_id="user-a"
    )
    retried = download_job_service.retry_download_job(
        job["id"], user_id="user-a", schedule=False
    )

    assert cancelled["status"] == "cancelled"
    assert cancelled["items"][0]["status"] == "cancelled"
    assert retried["id"] != job["id"]
    assert retried["status"] == "queued"
