"""Durable source-identity synchronization between downloads and LLM-Wiki.

Downloading audio and enriching knowledge remain separate lifecycles. New
successful downloads register identity and queue evidence-validated enrichment.
Legacy source-only rows are NOT silently backfilled on startup.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT, settings
from services.sqlite_runtime import connect_database


DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "wiki-sync.db"
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect(db_path: str | Path | None = None):
    with _LOCK:
        connection = _open_connection(db_path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _open_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect_database(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS wiki_sync_jobs (
            id TEXT PRIMARY KEY,
            source_key TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(wiki_sync_jobs)")}
    for name, definition in {
        "enrichment_status": "TEXT NOT NULL DEFAULT 'not_requested'",
        "enrichment_attempts": "INTEGER NOT NULL DEFAULT 0",
        "enrichment_error": "TEXT NOT NULL DEFAULT ''",
        "enrichment_result_json": "TEXT NOT NULL DEFAULT '{}'",
        "next_attempt_at": "REAL NOT NULL DEFAULT 0",
        "wiki_dir": "TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE wiki_sync_jobs ADD COLUMN {name} {definition}")
    connection.commit()
    return connection


def _source_key(metadata: dict[str, Any]) -> str:
    bvid = str(metadata.get("bvid") or "").strip()
    if bvid:
        return f"bilibili:{bvid}"
    local_path = str(metadata.get("local_file_path") or "").strip()
    if not local_path:
        raise ValueError("Wiki 同步元数据缺少 bvid 和 local_file_path")
    return f"local:{Path(local_path).resolve()}"


def enqueue_wiki_sync(
    metadata: dict[str, Any], *, db_path: str | Path | None = None
) -> dict[str, Any]:
    source_key = _source_key(metadata)
    now = _now()
    with _LOCK, _connect(db_path) as connection:
        existing = connection.execute(
            "SELECT * FROM wiki_sync_jobs WHERE source_key = ?", (source_key,)
        ).fetchone()
        if existing is not None:
            connection.execute(
                "UPDATE wiki_sync_jobs SET payload_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), now, existing["id"]),
            )
            connection.commit()
            return dict(existing) | {"payload_json": json.dumps(metadata, ensure_ascii=False)}

        job_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO wiki_sync_jobs
                (id, source_key, payload_json, status, attempts, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', 0, ?, ?)
            """,
            (job_id, source_key, json.dumps(metadata, ensure_ascii=False), now, now),
        )
        connection.commit()
        return {
            "id": job_id,
            "source_key": source_key,
            "status": "pending",
            "attempts": 0,
        }


def process_wiki_sync_job(
    job_id: str,
    *,
    db_path: str | Path | None = None,
    wiki_dir: str | None = None,
) -> dict[str, Any]:
    with _LOCK, _connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM wiki_sync_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown_wiki_sync_job")
        metadata = json.loads(row["payload_json"])
        attempts = int(row["attempts"]) + 1
        connection.execute(
            "UPDATE wiki_sync_jobs SET status = 'running', attempts = ?, updated_at = ? WHERE id = ?",
            (attempts, _now(), job_id),
        )
        connection.commit()

    try:
        from services.wiki_ingest import register_source_asset

        result = register_source_asset(metadata, wiki_dir or settings.WIKI_DIR)
        status = "completed"
        error = ""
    except Exception as exc:
        result = {}
        status = "failed"
        error = str(exc)

    with _LOCK, _connect(db_path) as connection:
        connection.execute(
            """
            UPDATE wiki_sync_jobs
            SET status = ?, last_error = ?, result_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error, json.dumps(result, ensure_ascii=False), _now(), job_id),
        )
        connection.commit()
    return {
        "id": job_id,
        "status": status,
        "attempts": attempts,
        "error": error,
        "result": result,
    }


def sync_downloaded_sources(
    records: list[dict[str, Any]],
    *,
    db_path: str | Path | None = None,
    wiki_dir: str | None = None,
) -> dict[str, Any]:
    """Persist intent only; download completion never waits for a Wiki writer/LLM."""
    jobs = []
    for record in records:
        job = enqueue_wiki_sync(record, db_path=db_path)
        target = str(Path(wiki_dir or settings.WIKI_DIR).resolve())
        with _LOCK, _connect(db_path) as connection:
            # Enrichment is authorized by THIS successful download, not migration.
            connection.execute("UPDATE wiki_sync_jobs SET enrichment_status='queued', wiki_dir=? WHERE id=? AND enrichment_status='not_requested'", (target, job["id"]))
        jobs.append(get_wiki_sync_job(job["id"], db_path=db_path))
    statuses = {job["status"] for job in jobs}
    overall = "completed" if statuses <= {"completed"} else "queued"
    return {"status": overall, "jobs": jobs}


def get_wiki_sync_job(job_id: str, *, db_path=None) -> dict[str, Any]:
    with _connect(db_path) as connection:
        row = connection.execute("SELECT * FROM wiki_sync_jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise LookupError("unknown_wiki_sync_job")
    return {"id": row["id"], "status": row["status"], "enrichment_status": row["enrichment_status"],
        "error": row["last_error"], "result": json.loads(row["result_json"]),
        "enrichment_attempts": row["enrichment_attempts"], "enrichment_error": row["enrichment_error"],
        "enrichment_result": json.loads(row["enrichment_result_json"])}


def recover_wiki_enrichment(*, db_path=None):
    with _connect(db_path) as connection:
        connection.execute("UPDATE wiki_sync_jobs SET enrichment_status='queued' WHERE enrichment_status='running'")


def cancel_wiki_enrichment(wiki_dir: str, *, db_path=None):
    """Called under the Wiki write lock on reset: never repopulate a reset Wiki."""
    with _connect(db_path) as connection:
        connection.execute("UPDATE wiki_sync_jobs SET enrichment_status='cancelled' WHERE wiki_dir=? AND enrichment_status IN ('queued','running','failed')", (str(Path(wiki_dir).resolve()),))


def retry_wiki_enrichment(job_id: str, *, db_path=None):
    with _connect(db_path) as connection:
        changed = connection.execute("UPDATE wiki_sync_jobs SET enrichment_status='queued', enrichment_attempts=0, next_attempt_at=0, enrichment_error='' WHERE id=? AND enrichment_status IN ('failed','needs_review')", (job_id,)).rowcount
    if not changed:
        raise ValueError("当前知识构建任务不可重试")
    return get_wiki_sync_job(job_id, db_path=db_path)


def process_next_wiki_enrichment(*, db_path=None) -> dict | None:
    """Claim one durable task. Serial writes share the reset lock with manual ingest."""
    from services.wiki_manager import _WIKI_RESET_LOCK
    from services.wiki_ingest import ingest_song
    with _WIKI_RESET_LOCK:
        with _connect(db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM wiki_sync_jobs WHERE enrichment_status='queued' AND next_attempt_at<=? ORDER BY created_at LIMIT 1", (time.time(),)).fetchone()
            if row is None:
                return None
            attempts = row["enrichment_attempts"] + 1
            connection.execute("UPDATE wiki_sync_jobs SET enrichment_status='running', enrichment_attempts=? WHERE id=?", (attempts, row["id"]))
        try:
            metadata = json.loads(row["payload_json"])
            path = Path(str(metadata.get("local_file_path") or ""))
            if not path.is_file():
                raise ValueError("本地音频不存在，未构建歌曲知识")
            source = process_wiki_sync_job(row["id"], db_path=db_path, wiki_dir=row["wiki_dir"])
            if source["status"] != "completed":
                raise RuntimeError(source["error"] or "原始来源登记失败")
            result = ingest_song(metadata, row["wiki_dir"])
            if result.get("extraction_status") == "failed":
                raise RuntimeError("模型抽取未通过结构或证据校验；已保留待核实页面")
            status = "needs_review" if result.get("verification_status") == "needs_review" else "completed"
            error = ""
        except Exception as exc:
            status = "queued" if attempts < 3 else "failed"
            result = {}
            error = str(exc)[:500]
        with _connect(db_path) as connection:
            connection.execute("UPDATE wiki_sync_jobs SET enrichment_status=?, enrichment_result_json=?, enrichment_error=?, next_attempt_at=?, updated_at=? WHERE id=?",
                (status, json.dumps(result, ensure_ascii=False), error, time.time() + 60 * attempts if status == "queued" else 0, _now(), row["id"]))
        return get_wiki_sync_job(row["id"], db_path=db_path)
