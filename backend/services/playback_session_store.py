"""Persistent playback-session state.

The playback session is the internal execution plan behind the user-facing
"currently playing" and "up next" views.  It is intentionally separate from
named playlists, which are durable collections and are not implemented by
this store.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from config import PROJECT_ROOT
from services.sqlite_runtime import connect_database


_DB_DIR = PROJECT_ROOT / "memory" / "data"
_DB_PATH = _DB_DIR / "playlist.db"
DEFAULT_USER_ID = "local"


class RevisionConflictError(RuntimeError):
    """Raised when a client attempts to overwrite a newer session snapshot."""

    def __init__(self, expected: int, actual: int):
        super().__init__(f"playback session revision conflict: expected {expected}, actual {actual}")
        self.expected = expected
        self.actual = actual


def _get_conn() -> sqlite3.Connection:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    return connect_database(_DB_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_id(user_id: str) -> str:
    return f"playback:{user_id}"


def _ensure_session(conn: sqlite3.Connection, user_id: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO playback_sessions (
            user_id, session_id, revision, current_item_id, status,
            order_mode, repeat_mode, progress_seconds, volume, updated_at
        ) VALUES (?, ?, 0, NULL, 'stopped', 'sequential', 'off', 0, 0.8, ?)
        """,
        (user_id, _session_id(user_id), _now()),
    )


def _ensure_navigation_columns(conn: sqlite3.Connection) -> None:
    """Add persisted navigation state without replacing existing sessions."""
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(playback_sessions)").fetchall()
    }
    if "history_item_ids_json" not in columns:
        conn.execute(
            "ALTER TABLE playback_sessions "
            "ADD COLUMN history_item_ids_json TEXT NOT NULL DEFAULT '[]'"
        )
    if "history_cursor" not in columns:
        conn.execute(
            "ALTER TABLE playback_sessions "
            "ADD COLUMN history_cursor INTEGER NOT NULL DEFAULT -1"
        )
    if "shuffle_bag_item_ids_json" not in columns:
        conn.execute(
            "ALTER TABLE playback_sessions "
            "ADD COLUMN shuffle_bag_item_ids_json TEXT NOT NULL DEFAULT '[]'"
        )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _migrate_legacy_playlist(conn: sqlite3.Connection) -> None:
    """Copy the old singleton ``playlist`` rows into the default session once."""
    migration_name = "legacy_playlist_to_playback_session_v1"
    migrated = conn.execute(
        "SELECT 1 FROM playback_migrations WHERE name = ?", (migration_name,)
    ).fetchone()
    if migrated:
        return
    if not _table_exists(conn, "playlist"):
        conn.execute(
            "INSERT INTO playback_migrations (name, applied_at) VALUES (?, ?)",
            (migration_name, _now()),
        )
        return
    existing = conn.execute(
        "SELECT COUNT(*) AS count FROM playback_session_items WHERE user_id = ?",
        (DEFAULT_USER_ID,),
    ).fetchone()
    if not existing or not existing["count"]:
        rows = conn.execute("SELECT * FROM playlist ORDER BY position").fetchall()
        for position, row in enumerate(rows):
            track_id = str(row["id"] or "")
            item_id = str(uuid5(NAMESPACE_URL, f"musicer:legacy:{position}:{track_id}"))
            conn.execute(
                """
                INSERT INTO playback_session_items (
                    item_id, user_id, track_id, title, author, url, filename,
                    bvid, duration, sub_dir, size, date, position,
                    origin_type, origin_id, added_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, 'legacy', NULL, ?)
                """,
                (
                    item_id,
                    DEFAULT_USER_ID,
                    track_id,
                    row["title"],
                    row["author"],
                    row["url"],
                    row["filename"],
                    row["bvid"],
                    row["duration"],
                    row["sub_dir"],
                    position,
                    _now(),
                ),
            )
    conn.execute(
        "INSERT INTO playback_migrations (name, applied_at) VALUES (?, ?)",
        (migration_name, _now()),
    )


def init_playback_session_db() -> None:
    conn = _get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS playback_sessions (
                user_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL UNIQUE,
                revision INTEGER NOT NULL DEFAULT 0,
                current_item_id TEXT,
                status TEXT NOT NULL DEFAULT 'stopped',
                order_mode TEXT NOT NULL DEFAULT 'sequential',
                repeat_mode TEXT NOT NULL DEFAULT 'off',
                progress_seconds REAL NOT NULL DEFAULT 0,
                volume REAL NOT NULL DEFAULT 0.8,
                history_item_ids_json TEXT NOT NULL DEFAULT '[]',
                history_cursor INTEGER NOT NULL DEFAULT -1,
                shuffle_bag_item_ids_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS playback_migrations (
                name TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS playback_session_items (
                item_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL DEFAULT '',
                bvid TEXT NOT NULL DEFAULT '',
                duration TEXT NOT NULL DEFAULT '',
                sub_dir TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                date TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL,
                origin_type TEXT NOT NULL DEFAULT 'manual',
                origin_id TEXT,
                added_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES playback_sessions(user_id) ON DELETE CASCADE,
                UNIQUE (user_id, position)
            );

            CREATE INDEX IF NOT EXISTS idx_playback_items_user_position
            ON playback_session_items(user_id, position);
            """
        )
        _ensure_navigation_columns(conn)
        _ensure_session(conn, DEFAULT_USER_ID)
        _migrate_legacy_playlist(conn)
        conn.commit()
    finally:
        conn.close()


def _track_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["track_id"],
        "title": row["title"],
        "author": row["author"],
        "date": row["date"],
        "filename": row["filename"],
        "subDir": row["sub_dir"],
        "size": row["size"],
        "url": row["url"],
        "bvid": row["bvid"] or None,
    }


def get_playback_session(user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
    user_id = user_id.strip() or DEFAULT_USER_ID
    init_playback_session_db()
    conn = _get_conn()
    try:
        _ensure_session(conn, user_id)
        session = conn.execute(
            "SELECT * FROM playback_sessions WHERE user_id = ?", (user_id,)
        ).fetchone()
        rows = conn.execute(
            "SELECT * FROM playback_session_items WHERE user_id = ? ORDER BY position",
            (user_id,),
        ).fetchall()
        conn.commit()
        assert session is not None
        item_ids = {str(row["item_id"]) for row in rows}
        history_item_ids = [
            str(item_id)
            for item_id in json.loads(session["history_item_ids_json"] or "[]")
            if str(item_id) in item_ids
        ]
        history_cursor = int(session["history_cursor"] or 0)
        if not history_item_ids:
            history_cursor = -1
        else:
            history_cursor = max(0, min(history_cursor, len(history_item_ids) - 1))
        shuffle_bag_item_ids = []
        seen_shuffle_ids: set[str] = set()
        for item_id in json.loads(session["shuffle_bag_item_ids_json"] or "[]"):
            normalized_id = str(item_id)
            if normalized_id not in item_ids or normalized_id in seen_shuffle_ids:
                continue
            seen_shuffle_ids.add(normalized_id)
            shuffle_bag_item_ids.append(normalized_id)
        return {
            "id": session["session_id"],
            "user_id": user_id,
            "revision": session["revision"],
            "current_item_id": session["current_item_id"],
            "status": session["status"],
            "order_mode": session["order_mode"],
            "repeat_mode": session["repeat_mode"],
            "progress_seconds": session["progress_seconds"],
            "volume": session["volume"],
            "history_item_ids": history_item_ids,
            "history_cursor": history_cursor,
            "shuffle_bag_item_ids": shuffle_bag_item_ids,
            "items": [
                {
                    "id": row["item_id"],
                    "position": row["position"],
                    "origin_type": row["origin_type"],
                    "origin_id": row["origin_id"],
                    "added_at": row["added_at"],
                    "track": _track_from_row(row),
                }
                for row in rows
            ],
        }
    finally:
        conn.close()


def replace_playback_session(
    *,
    user_id: str = DEFAULT_USER_ID,
    items: list[dict[str, Any]],
    current_item_id: str | None,
    status: str,
    order_mode: str,
    repeat_mode: str,
    progress_seconds: float,
    volume: float,
    history_item_ids: list[str] | None = None,
    history_cursor: int = -1,
    shuffle_bag_item_ids: list[str] | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Atomically replace a playback-session snapshot and increment its revision."""
    user_id = user_id.strip() or DEFAULT_USER_ID
    init_playback_session_db()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_session(conn, user_id)
        row = conn.execute(
            "SELECT revision FROM playback_sessions WHERE user_id = ?", (user_id,)
        ).fetchone()
        actual_revision = int(row["revision"] if row else 0)
        if expected_revision is not None and expected_revision != actual_revision:
            raise RevisionConflictError(expected_revision, actual_revision)

        normalized: list[dict[str, Any]] = []
        seen_item_ids: set[str] = set()
        for position, item in enumerate(items):
            track = item.get("track") if isinstance(item.get("track"), dict) else {}
            track_id = str(track.get("id") or "").strip()
            if not track_id:
                raise ValueError("playback session item is missing track.id")
            item_id = str(item.get("id") or uuid4())
            if item_id in seen_item_ids:
                raise ValueError("duplicate playback session item id")
            seen_item_ids.add(item_id)
            normalized.append(
                {
                    "id": item_id,
                    "position": position,
                    "origin_type": str(item.get("origin_type") or "manual"),
                    "origin_id": item.get("origin_id"),
                    "added_at": str(item.get("added_at") or _now()),
                    "track": track,
                }
            )

        if current_item_id and current_item_id not in seen_item_ids:
            current_item_id = None
            status = "stopped"
            progress_seconds = 0

        normalized_history = [
            str(item_id)
            for item_id in (history_item_ids or [])
            if str(item_id) in seen_item_ids
        ]
        if current_item_id:
            if not normalized_history:
                normalized_history = [current_item_id]
                history_cursor = 0
            else:
                history_cursor = max(0, min(int(history_cursor), len(normalized_history) - 1))
                if normalized_history[history_cursor] != current_item_id:
                    normalized_history = normalized_history[: history_cursor + 1]
                    normalized_history.append(current_item_id)
                    history_cursor = len(normalized_history) - 1
        else:
            normalized_history = []
            history_cursor = -1

        normalized_shuffle_bag: list[str] = []
        seen_shuffle_ids: set[str] = set()
        for item_id in shuffle_bag_item_ids or []:
            normalized_id = str(item_id)
            if (
                normalized_id not in seen_item_ids
                or normalized_id == current_item_id
                or normalized_id in seen_shuffle_ids
            ):
                continue
            seen_shuffle_ids.add(normalized_id)
            normalized_shuffle_bag.append(normalized_id)

        conn.execute("DELETE FROM playback_session_items WHERE user_id = ?", (user_id,))
        for item in normalized:
            track = item["track"]
            conn.execute(
                """
                INSERT INTO playback_session_items (
                    item_id, user_id, track_id, title, author, url, filename,
                    bvid, duration, sub_dir, size, date, position,
                    origin_type, origin_id, added_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    user_id,
                    str(track.get("id") or ""),
                    str(track.get("title") or ""),
                    str(track.get("author") or ""),
                    str(track.get("url") or ""),
                    str(track.get("filename") or ""),
                    str(track.get("bvid") or ""),
                    str(track.get("duration") or ""),
                    str(track.get("subDir", track.get("sub_dir", "")) or ""),
                    int(track.get("size") or 0),
                    str(track.get("date") or ""),
                    item["position"],
                    item["origin_type"],
                    item["origin_id"],
                    item["added_at"],
                ),
            )

        next_revision = actual_revision + 1
        conn.execute(
            """
            UPDATE playback_sessions
            SET revision = ?, current_item_id = ?, status = ?, order_mode = ?,
                repeat_mode = ?, progress_seconds = ?, volume = ?,
                history_item_ids_json = ?, history_cursor = ?,
                shuffle_bag_item_ids_json = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (
                next_revision,
                current_item_id,
                status,
                order_mode,
                repeat_mode,
                max(0.0, float(progress_seconds)),
                min(1.0, max(0.0, float(volume))),
                json.dumps(normalized_history, ensure_ascii=False),
                history_cursor,
                json.dumps(normalized_shuffle_bag, ensure_ascii=False),
                _now(),
                user_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_playback_session(user_id)


def get_legacy_tracks(user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
    return [item["track"] for item in get_playback_session(user_id)["items"]]


def replace_legacy_tracks(
    tracks: list[dict[str, Any]], user_id: str = DEFAULT_USER_ID
) -> dict[str, Any]:
    current = get_playback_session(user_id)
    return replace_playback_session(
        user_id=user_id,
        items=[{"track": track, "origin_type": "legacy"} for track in tracks],
        current_item_id=None,
        status="stopped",
        order_mode=current["order_mode"],
        repeat_mode=current["repeat_mode"],
        progress_seconds=0,
        volume=current["volume"],
        expected_revision=current["revision"],
    )
