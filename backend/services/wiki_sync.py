"""Durable source-identity synchronization between downloads and LLM-Wiki.

Downloading audio and enriching knowledge remain separate lifecycles.  A
successful download automatically registers only the attributable source asset
and leaves semantic enrichment pending for the llm-wiki workflow.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT, settings


DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "wiki-sync.db"
_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
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
    jobs = []
    for record in records:
        job = enqueue_wiki_sync(record, db_path=db_path)
        jobs.append(
            process_wiki_sync_job(
                str(job["id"]), db_path=db_path, wiki_dir=wiki_dir
            )
        )
    statuses = {job["status"] for job in jobs}
    overall = "completed" if statuses <= {"completed"} else "partial" if "completed" in statuses else "failed"
    return {"status": overall, "jobs": jobs}
