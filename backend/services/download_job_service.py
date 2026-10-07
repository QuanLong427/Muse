"""Persistent background jobs for Bilibili downloads.

The job store is the authority for download state.  UI and Agent callers only
submit validated source identities, observe progress, request cancellation, or
retry terminal failures.  A single daemon worker deliberately serializes B站
downloads to avoid amplifying rate limits.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from config import PROJECT_ROOT, settings
from services.bili_downloader import download_bilibili_audio, extract_bvid
from services.sqlite_runtime import connect_database


DEFAULT_USER_ID = "local"
_DB_DIR = PROJECT_ROOT / "memory" / "data"
_DB_PATH = _DB_DIR / "download-jobs.db"
_initialized_path: str | None = None
_init_lock = threading.Lock()
_worker_lock = threading.Lock()
_worker_wakeup = threading.Event()
_worker_thread: threading.Thread | None = None

ACTIVE_STATUSES = {"queued", "running", "cancel_requested"}
TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    init_download_job_db()
    return connect_database(_DB_PATH)


def init_download_job_db() -> None:
    global _initialized_path
    resolved = str(_DB_PATH.resolve())
    if _initialized_path == resolved and _DB_PATH.exists():
        return
    with _init_lock:
        if _initialized_path == resolved and _DB_PATH.exists():
            return
        _DB_DIR.mkdir(parents=True, exist_ok=True)
        with connect_database(_DB_PATH) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS download_jobs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    total_items INTEGER NOT NULL,
                    completed_items INTEGER NOT NULL DEFAULT 0,
                    failed_items INTEGER NOT NULL DEFAULT 0,
                    progress REAL NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_download_jobs_user_created
                    ON download_jobs(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_download_jobs_status_created
                    ON download_jobs(status, created_at ASC);

                CREATE TABLE IF NOT EXISTS download_job_items (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    bvid TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    artist TEXT NOT NULL DEFAULT '',
                    uploader TEXT NOT NULL DEFAULT '',
                    video_title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress REAL NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES download_jobs(id) ON DELETE CASCADE,
                    UNIQUE(job_id, bvid),
                    UNIQUE(job_id, position)
                );
                CREATE INDEX IF NOT EXISTS idx_download_job_items_job_position
                    ON download_job_items(job_id, position);
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(download_jobs)")}
            if "target_playlist_id" not in columns:
                conn.execute("ALTER TABLE download_jobs ADD COLUMN target_playlist_id TEXT")
            if "idempotency_key" not in columns:
                conn.execute("ALTER TABLE download_jobs ADD COLUMN idempotency_key TEXT")
            if "playlist_order_json" not in columns:
                conn.execute("ALTER TABLE download_jobs ADD COLUMN playlist_order_json TEXT NOT NULL DEFAULT '{}'")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_download_job_idempotency ON download_jobs(user_id, idempotency_key)")
        _initialized_path = resolved


def _json_object(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _job_from_conn(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM download_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    items = conn.execute(
        "SELECT * FROM download_job_items WHERE job_id = ? ORDER BY position",
        (job_id,),
    ).fetchall()
    result = _json_object(row["result_json"])
    wiki_sync = result.get("wiki_sync")
    if isinstance(wiki_sync, dict):
        from services.wiki_sync import get_wiki_sync_job
        for item in wiki_sync.get("jobs", []):
            if isinstance(item, dict) and item.get("id"):
                try:
                    item.update(get_wiki_sync_job(item["id"]))
                except LookupError:
                    pass
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "status": row["status"],
        "target_playlist_id": row["target_playlist_id"],
        "playlist_order": _json_object(row["playlist_order_json"]),
        "total_items": row["total_items"],
        "completed_items": row["completed_items"],
        "failed_items": row["failed_items"],
        "progress": round(float(row["progress"]), 2),
        "cancel_requested": bool(row["cancel_requested"]),
        "result": result,
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "items": [
            {
                "id": item["id"],
                "position": item["position"],
                "bvid": item["bvid"],
                "url": item["url"],
                "title": item["title"],
                "artist": item["artist"],
                "uploader": item["uploader"],
                "video_title": item["video_title"],
                "status": item["status"],
                "progress": round(float(item["progress"]), 2),
                "result": _json_object(item["result_json"]),
                "error": _json_object(item["error_json"]),
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
            for item in items
        ],
    }


def get_download_job(
    job_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any] | None:
    with _connect() as conn:
        job = _job_from_conn(conn, job_id)
        if job is None or job["user_id"] != (user_id.strip() or DEFAULT_USER_ID):
            return None
        return job


def list_download_jobs(
    *,
    user_id: str = DEFAULT_USER_ID,
    limit: int = 50,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    with _connect() as conn:
        params: list[Any] = [user_id.strip() or DEFAULT_USER_ID]
        where = "user_id = ?"
        if active_only:
            placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
            where += f" AND status IN ({placeholders})"
            params.extend(sorted(ACTIVE_STATUSES))
        params.append(max(1, min(int(limit), 200)))
        rows = conn.execute(
            f"SELECT id FROM download_jobs WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [
            job
            for row in rows
            if (job := _job_from_conn(conn, str(row["id"]))) is not None
        ]


def _normalize_items(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not 1 <= len(items) <= 50:
        raise ValueError("download items must contain between 1 and 50 entries")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("each download item must be an object")
        bvid = str(raw.get("bvid") or "").strip()
        url = str(raw.get("url") or f"https://www.bilibili.com/video/{bvid}").strip()
        parsed_bvid = extract_bvid(url)
        if bvid and bvid != parsed_bvid:
            raise ValueError("download item bvid does not match its URL")
        bvid = parsed_bvid
        if bvid in seen:
            continue
        seen.add(bvid)
        normalized.append(
            {
                "bvid": bvid,
                "url": f"https://www.bilibili.com/video/{bvid}",
                "title": str(raw.get("title") or "").strip()[:300],
                "artist": str(raw.get("artist") or "").strip()[:300],
                "uploader": str(raw.get("uploader") or "").strip()[:300],
                "video_title": str(
                    raw.get("videoTitle") or raw.get("video_title") or ""
                ).strip()[:1000],
            }
        )
    if not normalized:
        raise ValueError("download items contain no unique BVIDs")
    return normalized


def create_download_job(
    *,
    user_id: str,
    items: list[dict[str, Any]],
    schedule: bool = True,
    target_playlist_id: str | None = None,
    idempotency_key: str | None = None,
    playlist_order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_items(items)
    job_id = str(uuid4())
    owner = user_id.strip() or DEFAULT_USER_ID
    now = _now()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if idempotency_key:
            existing = conn.execute("SELECT id FROM download_jobs WHERE user_id = ? AND idempotency_key = ?", (owner, idempotency_key)).fetchone()
            if existing:
                return _job_from_conn(conn, existing["id"])
        conn.execute(
            """
            INSERT INTO download_jobs (
                id, user_id, status, total_items, created_at, updated_at, target_playlist_id, idempotency_key, playlist_order_json
            ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)
            """,
            (job_id, owner, len(normalized), now, now, target_playlist_id, idempotency_key, json.dumps(playlist_order or {}, ensure_ascii=False)),
        )
        for position, item in enumerate(normalized):
            conn.execute(
                """
                INSERT INTO download_job_items (
                    id, job_id, position, bvid, url, title, artist, uploader,
                    video_title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    job_id,
                    position,
                    item["bvid"],
                    item["url"],
                    item["title"],
                    item["artist"],
                    item["uploader"],
                    item["video_title"],
                    now,
                    now,
                ),
            )
        job = _job_from_conn(conn, job_id)
    if schedule:
        _ensure_worker()
        _worker_wakeup.set()
    assert job is not None
    return job


def _place_downloaded_items(job: dict, downloaded_bvids: set[str]) -> None:
    """Place only this job's remote items; preserve existing items' relative order."""
    from services.music_library_store import get_playlist, reorder_playlist_items
    playlist = get_playlist(job["target_playlist_id"], job["user_id"])
    if playlist is None:
        raise LookupError("target playlist deleted")
    plan = job["playlist_order"]
    def identity(item):
        track = item["track"]
        return f"bvid:{track['bvid']}" if track.get("bvid") else f"id:{track['id']}"
    by_key = {identity(item): item for item in playlist["items"]}
    moving = {item["id"] for item in playlist["items"] if item["track"].get("bvid") in downloaded_bvids}
    ordered = [item["id"] for item in playlist["items"] if item["id"] not in moving]
    anchor = plan.get("anchor") or ""
    for key in plan.get("items", []):
        item = by_key.get(key)
        if not item:
            continue
        if item["id"] in moving:
            if anchor in ordered:
                index = ordered.index(anchor) + 1
            else:
                following = next((by_key[k]["id"] for k in plan["items"] if k in by_key and by_key[k]["id"] in ordered), None)
                index = ordered.index(following) if following else len(ordered)
            ordered.insert(index, item["id"])
        anchor = item["id"]
    if ordered != [item["id"] for item in playlist["items"]]:
        reorder_playlist_items(playlist["id"], ordered, expected_revision=playlist["revision"], user_id=job["user_id"])


def _refresh_job_progress(conn: sqlite3.Connection, job_id: str) -> None:
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'downloaded' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
               AVG(progress) AS progress
        FROM download_job_items WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()
    conn.execute(
        """
        UPDATE download_jobs
        SET completed_items = ?, failed_items = ?, progress = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            int(row["completed"] or 0),
            int(row["failed"] or 0),
            float(row["progress"] or 0.0),
            _now(),
            job_id,
        ),
    )


def _record_item_event(job_id: str, event: dict[str, Any]) -> None:
    bvid = str(event.get("bvid") or "")
    if not bvid:
        return
    status = str(event.get("status") or "downloading")
    if status not in {"queued", "downloading", "downloaded", "failed", "cancelled"}:
        status = "downloading"
    progress = max(0.0, min(float(event.get("progress") or 0.0), 100.0))
    error = event.get("error") if isinstance(event.get("error"), dict) else {}
    with _connect() as conn:
        conn.execute(
            """
            UPDATE download_job_items
            SET status = ?, progress = ?, error_json = ?, updated_at = ?
            WHERE job_id = ? AND bvid = ?
            """,
            (
                status,
                progress,
                json.dumps(error, ensure_ascii=False),
                _now(),
                job_id,
                bvid,
            ),
        )
        _refresh_job_progress(conn, job_id)


def _is_cancel_requested(job_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT cancel_requested FROM download_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        return row is None or bool(row["cancel_requested"])


def run_download_job(job_id: str) -> dict[str, Any] | None:
    """Run one queued job. Exposed for deterministic tests and recovery."""
    with _connect() as conn:
        job = _job_from_conn(conn, job_id)
        if job is None:
            return None
        if job["status"] not in {"queued", "running"}:
            return job
        if job["cancel_requested"]:
            conn.execute(
                """
                UPDATE download_jobs SET status = 'cancelled', finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (_now(), _now(), job_id),
            )
            return _job_from_conn(conn, job_id)
        conn.execute(
            """
            UPDATE download_jobs
            SET status = 'running', started_at = COALESCE(started_at, ?), updated_at = ?
            WHERE id = ?
            """,
            (_now(), _now(), job_id),
        )
        items = list(job["items"])

    metadata = [
        {
            "bvid": item["bvid"],
            "title": item["title"],
            "artist": item["artist"],
            "uploader": item["uploader"],
            "videoTitle": item["video_title"],
        }
        for item in items
    ]
    try:
        result = download_bilibili_audio(
            urls=[str(item["url"]) for item in items],
            metadata=metadata,
            music_dir=settings.MUSIC_DIR,
            cookie_file=settings.BILIBILI_COOKIES_FILE,
            timeout_seconds=settings.BILIBILI_DOWNLOAD_TIMEOUT_SECONDS,
            progress_callback=lambda event: _record_item_event(job_id, event),
            cancel_requested=lambda: _is_cancel_requested(job_id),
        )
    except Exception as exc:  # Defensive boundary around the external process.
        result = {
            "success": False,
            "status": "failed",
            "files": [],
            "errors": [
                {
                    "code": "download_runtime_error",
                    "message": str(exc)[:2000],
                    "retryable": True,
                }
            ],
        }

    from services.music_manager import find_track_by_bvid
    from services.wiki_sync import sync_downloaded_sources

    files_by_bvid = {
        str(item.get("bvid") or ""): item
        for item in result.get("files", [])
        if isinstance(item, dict)
    }
    errors_by_bvid = {
        str(item.get("bvid") or ""): item
        for item in result.get("errors", [])
        if isinstance(item, dict) and item.get("bvid")
    }
    global_error = next(
        (
            item
            for item in result.get("errors", [])
            if isinstance(item, dict) and not item.get("bvid")
        ),
        None,
    )
    local_tracks: list[dict[str, Any]] = []
    tracks_by_bvid: dict[str, dict[str, Any]] = {}
    for bvid in files_by_bvid:
        track = find_track_by_bvid(bvid)
        serialized_track = track.model_dump(mode="json") if track else None
        if serialized_track:
            local_tracks.append(serialized_track)
            tracks_by_bvid[bvid] = serialized_track

    # Only canonical local Tracks enter a durable playlist. This is idempotent
    # across worker recovery; deleted playlists are never recreated here.
    if job.get("target_playlist_id"):
        from services.music_library_store import add_playlist_track
        for bvid in files_by_bvid:
            try:
                canonical = tracks_by_bvid.get(bvid)
                if not canonical:
                    raise ValueError("下载完成但未找到本地 Track")
                add_playlist_track(job["target_playlist_id"], track=canonical,
                                   expected_revision=None, user_id=job["user_id"])
            except Exception as exc:
                error = {"bvid": bvid, "code": "playlist_add_failed",
                         "message": f"音频已下载，但加入歌单失败：{str(exc)[:300]}", "retryable": True}
                errors_by_bvid[bvid] = error
                result.setdefault("errors", []).append(error)

        if job.get("playlist_order") and files_by_bvid:
            try:
                _place_downloaded_items(job, set(files_by_bvid))
            except (LookupError, ValueError, RuntimeError) as exc:
                for bvid in files_by_bvid:
                    error = {"bvid": bvid, "code": "playlist_order_failed", "message": f"音频已下载，歌单排序未完成：{str(exc)[:200]}", "retryable": True}
                    errors_by_bvid[bvid] = error
                    result.setdefault("errors", []).append(error)

    cancelled = _is_cancel_requested(job_id) or result.get("status") == "cancelled"
    with _connect() as conn:
        for item in items:
            bvid = str(item["bvid"])
            file_result = files_by_bvid.get(bvid)
            error_result = errors_by_bvid.get(bvid)
            if file_result is not None and error_result is None:
                serialized_track = tracks_by_bvid.get(bvid)
                conn.execute(
                    """
                    UPDATE download_job_items
                    SET status = 'downloaded', progress = 100, result_json = ?, updated_at = ?
                    WHERE job_id = ? AND bvid = ?
                    """,
                    (
                        json.dumps(
                            {"file": file_result, "track": serialized_track},
                            ensure_ascii=False,
                            default=str,
                        ),
                        _now(),
                        job_id,
                        bvid,
                    ),
                )
            elif error_result is not None or global_error is not None:
                error_result = error_result or global_error or {}
                item_status = (
                    "cancelled"
                    if error_result.get("code") == "download_cancelled"
                    else "failed"
                )
                conn.execute(
                    """
                    UPDATE download_job_items
                    SET status = ?, error_json = ?, updated_at = ?
                    WHERE job_id = ? AND bvid = ?
                    """,
                    (
                        item_status,
                        json.dumps(error_result, ensure_ascii=False, default=str),
                        _now(),
                        job_id,
                        bvid,
                    ),
                )

        if cancelled:
            conn.execute(
                """
                UPDATE download_job_items
                SET status = 'cancelled', updated_at = ?
                WHERE job_id = ? AND status IN ('queued', 'downloading')
                """,
                (_now(), job_id),
            )
        else:
            fallback_error = global_error or {
                "code": "download_result_missing",
                "message": "下载器没有返回该条目的执行结果。",
                "retryable": True,
            }
            conn.execute(
                """
                UPDATE download_job_items
                SET status = 'failed', error_json = ?, updated_at = ?
                WHERE job_id = ? AND status IN ('queued', 'downloading')
                """,
                (
                    json.dumps(fallback_error, ensure_ascii=False, default=str),
                    _now(),
                    job_id,
                ),
            )
        _refresh_job_progress(conn, job_id)
        counts = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status = 'downloaded' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                   SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled
            FROM download_job_items WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
        completed = int(counts["completed"] or 0)
        cancelled_count = int(counts["cancelled"] or 0)
        total = int(counts["total"] or 0)
        if completed == total:
            status = "completed"
        elif cancelled_count:
            status = "cancelled"
        elif completed:
            status = "partial"
        else:
            status = "failed"

    if files_by_bvid:
        try:
            wiki_sync = sync_downloaded_sources(list(files_by_bvid.values()))
        except Exception as exc:  # Wiki projection does not undo a valid download.
            wiki_sync = {
                "status": "failed",
                "jobs": [],
                "error": str(exc)[:2000],
            }
    else:
        wiki_sync = {"status": "not_started", "jobs": []}
    stored_result = {
        **result,
        "tracks": local_tracks,
        "wiki_sync": wiki_sync,
    }
    last_error = ""
    if result.get("errors"):
        last = result["errors"][-1]
        if isinstance(last, dict):
            last_error = str(last.get("message") or last.get("code") or "")
    with _connect() as conn:
        conn.execute(
            """
            UPDATE download_jobs
            SET status = ?, progress = CASE WHEN ? = 'completed' THEN 100 ELSE progress END,
                result_json = ?, last_error = ?, finished_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                status,
                json.dumps(stored_result, ensure_ascii=False, default=str),
                last_error,
                _now(),
                _now(),
                job_id,
            ),
        )
        return _job_from_conn(conn, job_id)


def request_download_job_cancel(
    job_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any] | None:
    with _connect() as conn:
        job = _job_from_conn(conn, job_id)
        if job is None or job["user_id"] != (user_id.strip() or DEFAULT_USER_ID):
            return None
        if job["status"] in TERMINAL_STATUSES:
            return job
        now = _now()
        conn.execute(
            """
            UPDATE download_jobs
            SET cancel_requested = 1, status = 'cancel_requested', updated_at = ?
            WHERE id = ?
            """,
            (now, job_id),
        )
        if job["status"] == "queued":
            conn.execute(
                """
                UPDATE download_jobs
                SET status = 'cancelled', finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, job_id),
            )
            conn.execute(
                """
                UPDATE download_job_items SET status = 'cancelled', updated_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (now, job_id),
            )
        return _job_from_conn(conn, job_id)


def retry_download_job(
    job_id: str,
    *,
    user_id: str = DEFAULT_USER_ID,
    schedule: bool = True,
) -> dict[str, Any] | None:
    job = get_download_job(job_id, user_id=user_id)
    if job is None:
        return None
    if job["status"] not in TERMINAL_STATUSES:
        raise ValueError("only terminal download jobs can be retried")
    retry_items = [
        {
            "bvid": item["bvid"],
            "url": item["url"],
            "title": item["title"],
            "artist": item["artist"],
            "uploader": item["uploader"],
            "video_title": item["video_title"],
        }
        for item in job["items"]
        if item["status"] in {"failed", "cancelled"}
    ]
    if not retry_items:
        raise ValueError("download job has no failed or cancelled items")
    return create_download_job(
        user_id=user_id,
        items=retry_items,
        schedule=schedule,
        target_playlist_id=job.get("target_playlist_id"),
        playlist_order=job.get("playlist_order"),
    )


def _next_queued_job_id() -> str | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM download_jobs
            WHERE status = 'queued' AND cancel_requested = 0
            ORDER BY created_at ASC LIMIT 1
            """
        ).fetchone()
        return str(row["id"]) if row else None


def _worker_loop() -> None:
    while True:
        _worker_wakeup.wait()
        while True:
            job_id = _next_queued_job_id()
            if not job_id:
                _worker_wakeup.clear()
                if _next_queued_job_id():
                    _worker_wakeup.set()
                    continue
                break
            try:
                run_download_job(job_id)
            except Exception as exc:  # pragma: no cover - defensive worker boundary
                with _connect() as conn:
                    conn.execute(
                        """
                        UPDATE download_jobs
                        SET status = 'failed', last_error = ?, finished_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (str(exc)[:2000], _now(), _now(), job_id),
                    )


def _ensure_worker() -> None:
    global _worker_thread
    if _worker_thread is not None and _worker_thread.is_alive():
        return
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(
            target=_worker_loop,
            name="musicer-download-worker",
            daemon=True,
        )
        _worker_thread.start()


def init_download_job_system() -> None:
    """Recover interrupted jobs and start the serialized background worker."""
    init_download_job_db()
    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE download_jobs
            SET status = 'cancelled', finished_at = ?, updated_at = ?
            WHERE status = 'cancel_requested'
            """,
            (now, now),
        )
        conn.execute(
            """
            UPDATE download_job_items
            SET status = 'cancelled', updated_at = ?
            WHERE job_id IN (SELECT id FROM download_jobs WHERE status = 'cancelled')
              AND status IN ('queued', 'downloading')
            """,
            (now,),
        )
        conn.execute(
            """
            UPDATE download_jobs SET status = 'queued', updated_at = ?
            WHERE status = 'running'
            """,
            (now,),
        )
        conn.execute(
            """
            UPDATE download_job_items SET status = 'queued', progress = 0, updated_at = ?
            WHERE status = 'downloading'
            """,
            (now,),
        )
    _ensure_worker()
    if _next_queued_job_id():
        _worker_wakeup.set()
