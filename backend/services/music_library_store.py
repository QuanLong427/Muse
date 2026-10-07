"""Named playlists and observable playback-event persistence.

Named playlists are durable collections. Playback events are append-only facts.
Neither table owns or mutates the active PlaybackSession.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from config import PROJECT_ROOT
from services.sqlite_runtime import connect_database


_DB_DIR = PROJECT_ROOT / "memory" / "data"
_DB_PATH = _DB_DIR / "music-library.db"
DEFAULT_USER_ID = "local"


class PlaylistRevisionConflictError(RuntimeError):
    def __init__(self, expected: int, actual: int):
        super().__init__(f"playlist revision conflict: expected {expected}, actual {actual}")
        self.expected = expected
        self.actual = actual


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn() -> sqlite3.Connection:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    return connect_database(_DB_PATH)


def _ensure_music_library_columns(conn: sqlite3.Connection) -> None:
    """Apply additive migrations for existing single-user databases."""
    playback_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(playback_events)").fetchall()
    }
    if "scenario" not in playback_columns:
        conn.execute(
            "ALTER TABLE playback_events ADD COLUMN scenario TEXT NOT NULL DEFAULT '默认'"
        )


def init_music_library_db() -> None:
    conn = _get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS named_playlists (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL COLLATE NOCASE,
                description TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (user_id, name)
            );

            CREATE TABLE IF NOT EXISTS named_playlist_items (
                id TEXT PRIMARY KEY,
                playlist_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL,
                filename TEXT NOT NULL,
                bvid TEXT NOT NULL DEFAULT '',
                sub_dir TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                date TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                FOREIGN KEY (playlist_id) REFERENCES named_playlists(id) ON DELETE CASCADE,
                UNIQUE (playlist_id, track_id),
                UNIQUE (playlist_id, position)
            );

            CREATE INDEX IF NOT EXISTS idx_named_playlists_user_updated
            ON named_playlists(user_id, updated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_named_playlist_items_position
            ON named_playlist_items(playlist_id, position);

            CREATE TABLE IF NOT EXISTS playback_events (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                item_id TEXT,
                event_type TEXT NOT NULL,
                track_id TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL,
                filename TEXT NOT NULL,
                bvid TEXT NOT NULL DEFAULT '',
                sub_dir TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                date TEXT NOT NULL DEFAULT '',
                position_seconds REAL NOT NULL DEFAULT 0,
                duration_seconds REAL NOT NULL DEFAULT 0,
                origin_type TEXT NOT NULL DEFAULT 'manual',
                origin_id TEXT,
                scenario TEXT NOT NULL DEFAULT '默认',
                occurred_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_playback_events_user_time
            ON playback_events(user_id, occurred_at DESC);

            CREATE INDEX IF NOT EXISTS idx_playback_events_user_track
            ON playback_events(user_id, track_id, occurred_at DESC);

            CREATE TABLE IF NOT EXISTS track_feedback_events (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                track_id TEXT NOT NULL,
                feedback_type TEXT NOT NULL,
                scenario TEXT NOT NULL DEFAULT '默认',
                source TEXT NOT NULL DEFAULT 'user',
                occurred_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_track_feedback_user_track_time
            ON track_feedback_events(user_id, track_id, occurred_at DESC);

            CREATE TABLE IF NOT EXISTS recommendation_batches (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                scenario TEXT NOT NULL DEFAULT '默认',
                current_track_id TEXT,
                profile_json TEXT NOT NULL DEFAULT '{}',
                constraints_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_recommendation_batches_user_time
            ON recommendation_batches(user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS recommendation_batch_items (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                track_id TEXT NOT NULL,
                score REAL NOT NULL,
                reasons_json TEXT NOT NULL DEFAULT '[]',
                track_json TEXT NOT NULL,
                FOREIGN KEY (batch_id) REFERENCES recommendation_batches(id) ON DELETE CASCADE,
                UNIQUE (batch_id, position),
                UNIQUE (batch_id, track_id)
            );

            CREATE INDEX IF NOT EXISTS idx_recommendation_items_batch_position
            ON recommendation_batch_items(batch_id, position);
            """
        )
        _ensure_music_library_columns(conn)
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


def _playlist_from_conn(conn: sqlite3.Connection, playlist_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM named_playlists WHERE id = ?", (playlist_id,)
    ).fetchone()
    if row is None:
        return None
    items = conn.execute(
        "SELECT * FROM named_playlist_items WHERE playlist_id = ? ORDER BY position",
        (playlist_id,),
    ).fetchall()
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "name": row["name"],
        "description": row["description"],
        "revision": row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "items": [
            {
                "id": item["id"],
                "position": item["position"],
                "added_at": item["added_at"],
                "track": _track_from_row(item),
            }
            for item in items
        ],
    }


def list_playlists(user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
    init_music_library_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id FROM named_playlists WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id.strip() or DEFAULT_USER_ID,),
        ).fetchall()
        return [playlist for row in rows if (playlist := _playlist_from_conn(conn, row["id"]))]
    finally:
        conn.close()


def get_playlist(playlist_id: str, user_id: str = DEFAULT_USER_ID) -> dict[str, Any] | None:
    init_music_library_db()
    conn = _get_conn()
    try:
        playlist = _playlist_from_conn(conn, playlist_id)
        if playlist is None or playlist["user_id"] != (user_id.strip() or DEFAULT_USER_ID):
            return None
        return playlist
    finally:
        conn.close()


def create_playlist(
    name: str,
    description: str = "",
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    normalized_name = " ".join(name.split()).strip()
    if not normalized_name:
        raise ValueError("playlist name is required")
    init_music_library_db()
    playlist_id = str(uuid4())
    now = _now()
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO named_playlists
                (id, user_id, name, description, revision, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (playlist_id, user_id.strip() or DEFAULT_USER_ID, normalized_name, description.strip(), now, now),
        )
        conn.commit()
        playlist = _playlist_from_conn(conn, playlist_id)
        assert playlist is not None
        return playlist
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError("playlist name already exists") from exc
    finally:
        conn.close()


def _require_owned_playlist(
    conn: sqlite3.Connection,
    playlist_id: str,
    user_id: str,
    expected_revision: int | None,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM named_playlists WHERE id = ? AND user_id = ?",
        (playlist_id, user_id.strip() or DEFAULT_USER_ID),
    ).fetchone()
    if row is None:
        raise LookupError("playlist not found")
    actual = int(row["revision"])
    if expected_revision is not None and expected_revision != actual:
        raise PlaylistRevisionConflictError(expected_revision, actual)
    return row


def _touch_playlist(conn: sqlite3.Connection, playlist_id: str) -> None:
    conn.execute(
        "UPDATE named_playlists SET revision = revision + 1, updated_at = ? WHERE id = ?",
        (_now(), playlist_id),
    )


def update_playlist(
    playlist_id: str,
    *,
    name: str,
    description: str,
    expected_revision: int | None,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    normalized_name = " ".join(name.split()).strip()
    if not normalized_name:
        raise ValueError("playlist name is required")
    init_music_library_db()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_owned_playlist(conn, playlist_id, user_id, expected_revision)
        conn.execute(
            """
            UPDATE named_playlists
            SET name = ?, description = ?, revision = revision + 1, updated_at = ?
            WHERE id = ?
            """,
            (normalized_name, description.strip(), _now(), playlist_id),
        )
        conn.commit()
        playlist = _playlist_from_conn(conn, playlist_id)
        assert playlist is not None
        return playlist
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError("playlist name already exists") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_playlist(
    playlist_id: str,
    *,
    expected_revision: int | None,
    user_id: str = DEFAULT_USER_ID,
) -> None:
    init_music_library_db()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_owned_playlist(conn, playlist_id, user_id, expected_revision)
        conn.execute("DELETE FROM named_playlists WHERE id = ?", (playlist_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_playlist_track(
    playlist_id: str,
    *,
    track: dict[str, Any],
    expected_revision: int | None,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    track_id = str(track.get("id") or "").strip()
    if not track_id:
        raise ValueError("track.id is required")
    init_music_library_db()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_owned_playlist(conn, playlist_id, user_id, expected_revision)
        existing = conn.execute(
            "SELECT 1 FROM named_playlist_items WHERE playlist_id = ? AND track_id = ?",
            (playlist_id, track_id),
        ).fetchone()
        if existing is None:
            next_position = conn.execute(
                "SELECT COUNT(*) FROM named_playlist_items WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO named_playlist_items (
                    id, playlist_id, track_id, title, author, url, filename,
                    bvid, sub_dir, size, date, position, added_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    playlist_id,
                    track_id,
                    str(track.get("title") or ""),
                    str(track.get("author") or ""),
                    str(track.get("url") or ""),
                    str(track.get("filename") or ""),
                    str(track.get("bvid") or ""),
                    str(track.get("subDir", track.get("sub_dir", "")) or ""),
                    int(track.get("size") or 0),
                    str(track.get("date") or ""),
                    next_position,
                    _now(),
                ),
            )
            _touch_playlist(conn, playlist_id)
        conn.commit()
        playlist = _playlist_from_conn(conn, playlist_id)
        assert playlist is not None
        return playlist
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def remove_playlist_item(
    playlist_id: str,
    item_id: str,
    *,
    expected_revision: int | None,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    init_music_library_db()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_owned_playlist(conn, playlist_id, user_id, expected_revision)
        deleted = conn.execute(
            "DELETE FROM named_playlist_items WHERE id = ? AND playlist_id = ?",
            (item_id, playlist_id),
        ).rowcount
        if not deleted:
            raise LookupError("playlist item not found")
        rows = conn.execute(
            "SELECT id FROM named_playlist_items WHERE playlist_id = ? ORDER BY position",
            (playlist_id,),
        ).fetchall()
        conn.execute(
            "UPDATE named_playlist_items SET position = position + 100000 WHERE playlist_id = ?",
            (playlist_id,),
        )
        for position, row in enumerate(rows):
            conn.execute(
                "UPDATE named_playlist_items SET position = ? WHERE id = ?",
                (position, row["id"]),
            )
        _touch_playlist(conn, playlist_id)
        conn.commit()
        playlist = _playlist_from_conn(conn, playlist_id)
        assert playlist is not None
        return playlist
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reorder_playlist_items(
    playlist_id: str,
    item_ids: list[str],
    *,
    expected_revision: int | None,
    user_id: str = DEFAULT_USER_ID,
) -> dict[str, Any]:
    init_music_library_db()
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_owned_playlist(conn, playlist_id, user_id, expected_revision)
        existing = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM named_playlist_items WHERE playlist_id = ? ORDER BY position",
                (playlist_id,),
            ).fetchall()
        ]
        if len(item_ids) != len(set(item_ids)) or set(item_ids) != set(existing):
            raise ValueError("item_ids must contain every playlist item exactly once")
        conn.execute(
            "UPDATE named_playlist_items SET position = position + 100000 WHERE playlist_id = ?",
            (playlist_id,),
        )
        for position, item_id in enumerate(item_ids):
            conn.execute(
                "UPDATE named_playlist_items SET position = ? WHERE id = ? AND playlist_id = ?",
                (position, item_id, playlist_id),
            )
        _touch_playlist(conn, playlist_id)
        conn.commit()
        playlist = _playlist_from_conn(conn, playlist_id)
        assert playlist is not None
        return playlist
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_playback_event(
    *,
    user_id: str,
    session_id: str,
    item_id: str | None,
    event_type: str,
    track: dict[str, Any],
    position_seconds: float,
    duration_seconds: float,
    origin_type: str,
    origin_id: str | None,
    scenario: str = "默认",
) -> dict[str, Any]:
    track_id = str(track.get("id") or "").strip()
    if not track_id:
        raise ValueError("track.id is required")
    init_music_library_db()
    event_id = str(uuid4())
    occurred_at = _now()
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO playback_events (
                id, user_id, session_id, item_id, event_type,
                track_id, title, author, url, filename, bvid, sub_dir, size, date,
                position_seconds, duration_seconds, origin_type, origin_id, scenario, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                user_id.strip() or DEFAULT_USER_ID,
                session_id,
                item_id,
                event_type,
                track_id,
                str(track.get("title") or ""),
                str(track.get("author") or ""),
                str(track.get("url") or ""),
                str(track.get("filename") or ""),
                str(track.get("bvid") or ""),
                str(track.get("subDir", track.get("sub_dir", "")) or ""),
                int(track.get("size") or 0),
                str(track.get("date") or ""),
                max(0.0, float(position_seconds)),
                max(0.0, float(duration_seconds)),
                origin_type or "manual",
                origin_id,
                scenario.strip()[:80] or "默认",
                occurred_at,
            ),
        )
        conn.commit()
        return {"id": event_id, "occurred_at": occurred_at}
    finally:
        conn.close()


def list_recent_tracks(
    user_id: str = DEFAULT_USER_ID,
    limit: int = 50,
) -> list[dict[str, Any]]:
    init_music_library_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM playback_events
            WHERE user_id = ? AND event_type IN ('play_started', 'play_resumed', 'play_completed')
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (user_id.strip() or DEFAULT_USER_ID, max(limit * 10, limit)),
        ).fetchall()
        recent: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if row["track_id"] in seen:
                continue
            seen.add(row["track_id"])
            recent.append(
                {
                    "track": _track_from_row(row),
                    "last_played_at": row["occurred_at"],
                    "last_event_type": row["event_type"],
                    "position_seconds": row["position_seconds"],
                    "duration_seconds": row["duration_seconds"],
                }
            )
            if len(recent) >= limit:
                break
        return recent
    finally:
        conn.close()


def record_track_feedback(
    *,
    user_id: str,
    track_id: str,
    feedback_type: str,
    scenario: str = "默认",
    source: str = "user",
) -> dict[str, Any]:
    allowed = {
        "like",
        "dislike",
        "dislike_version",
        "not_now",
        "more_like_this",
        "replay",
        "favorite",
    }
    normalized_type = feedback_type.strip().lower()
    if normalized_type not in allowed:
        raise ValueError(f"unsupported feedback type: {feedback_type}")
    normalized_track_id = track_id.strip()
    if not normalized_track_id:
        raise ValueError("track_id is required")
    init_music_library_db()
    event = {
        "id": str(uuid4()),
        "user_id": user_id.strip() or DEFAULT_USER_ID,
        "track_id": normalized_track_id,
        "feedback_type": normalized_type,
        "scenario": scenario.strip()[:80] or "默认",
        "source": source.strip()[:40] or "user",
        "occurred_at": _now(),
    }
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO track_feedback_events(
                id, user_id, track_id, feedback_type, scenario, source, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                event["user_id"],
                event["track_id"],
                event["feedback_type"],
                event["scenario"],
                event["source"],
                event["occurred_at"],
            ),
        )
        conn.commit()
        return event
    finally:
        conn.close()


def get_track_feedback_summary(
    *, user_id: str = DEFAULT_USER_ID, track_id: str | None = None
) -> list[dict[str, Any]]:
    init_music_library_db()
    conn = _get_conn()
    try:
        sql = "SELECT * FROM track_feedback_events WHERE user_id = ?"
        params: list[Any] = [user_id.strip() or DEFAULT_USER_ID]
        if track_id:
            sql += " AND track_id = ?"
            params.append(track_id)
        sql += " ORDER BY occurred_at DESC"
        rows = conn.execute(sql, params).fetchall()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = grouped.setdefault(
                str(row["track_id"]),
                {
                    "track_id": str(row["track_id"]),
                    "latest_feedback": str(row["feedback_type"]),
                    "latest_at": str(row["occurred_at"]),
                    "counts": {},
                },
            )
            feedback = str(row["feedback_type"])
            item["counts"][feedback] = int(item["counts"].get(feedback, 0)) + 1
        return list(grouped.values())
    finally:
        conn.close()


def list_feedback_excluded_track_ids(user_id: str = DEFAULT_USER_ID) -> set[str]:
    """Return tracks whose latest explicit preference is negative.

    `not_now` remains active for 24 hours. A later like/favorite/replay clears
    the exclusion without deleting the underlying evidence event.
    """
    init_music_library_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM track_feedback_events
            WHERE user_id = ?
            ORDER BY occurred_at DESC
            """,
            (user_id.strip() or DEFAULT_USER_ID,),
        ).fetchall()
        latest: dict[str, sqlite3.Row] = {}
        for row in rows:
            latest.setdefault(str(row["track_id"]), row)
        now = datetime.now(timezone.utc)
        excluded: set[str] = set()
        for track_id, row in latest.items():
            feedback = str(row["feedback_type"])
            if feedback in {"dislike", "dislike_version"}:
                excluded.add(track_id)
            elif feedback == "not_now":
                occurred = datetime.fromisoformat(str(row["occurred_at"]))
                if (now - occurred).total_seconds() < 24 * 60 * 60:
                    excluded.add(track_id)
        return excluded
    finally:
        conn.close()


def list_playback_events_since(
    *, user_id: str = DEFAULT_USER_ID, since: str
) -> list[dict[str, Any]]:
    """Return immutable playback evidence at or after an ISO timestamp."""
    init_music_library_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM playback_events
            WHERE user_id = ? AND occurred_at >= ?
            ORDER BY occurred_at ASC, id ASC
            """,
            (user_id.strip() or DEFAULT_USER_ID, since),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_track_feedback_events_since(
    *, user_id: str = DEFAULT_USER_ID, since: str
) -> list[dict[str, Any]]:
    """Return immutable explicit-feedback evidence at or after an ISO timestamp."""
    init_music_library_db()
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT * FROM track_feedback_events
            WHERE user_id = ? AND occurred_at >= ?
            ORDER BY occurred_at ASC, id ASC
            """,
            (user_id.strip() or DEFAULT_USER_ID, since),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def record_recommendation_batch(
    *,
    user_id: str,
    kind: str,
    scenario: str,
    current_track_id: str | None,
    profile_snapshot: dict[str, Any],
    constraints: dict[str, Any],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist one immutable recommendation decision and its ranked items."""
    if not items:
        raise ValueError("recommendation batch requires at least one item")
    init_music_library_db()
    batch_id = str(uuid4())
    created_at = _now()
    normalized_user = user_id.strip() or DEFAULT_USER_ID
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO recommendation_batches(
                id, user_id, kind, scenario, current_track_id,
                profile_json, constraints_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                normalized_user,
                kind.strip() or "radio",
                scenario.strip()[:80] or "默认",
                current_track_id or None,
                json.dumps(profile_snapshot, ensure_ascii=False),
                json.dumps(constraints, ensure_ascii=False),
                created_at,
            ),
        )
        for position, item in enumerate(items):
            track = item.get("track")
            if not isinstance(track, dict) or not str(track.get("id") or "").strip():
                raise ValueError("recommendation item requires track.id")
            conn.execute(
                """
                INSERT INTO recommendation_batch_items(
                    id, batch_id, position, track_id, score, reasons_json, track_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    batch_id,
                    position,
                    str(track["id"]),
                    float(item.get("score") or 0.0),
                    json.dumps(item.get("reasons") or [], ensure_ascii=False),
                    json.dumps(track, ensure_ascii=False),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    batch = get_recommendation_batch(batch_id, user_id=normalized_user)
    assert batch is not None
    return batch


def get_recommendation_batch(
    batch_id: str, *, user_id: str = DEFAULT_USER_ID
) -> dict[str, Any] | None:
    init_music_library_db()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM recommendation_batches WHERE id = ? AND user_id = ?",
            (batch_id, user_id.strip() or DEFAULT_USER_ID),
        ).fetchone()
        if row is None:
            return None
        items = conn.execute(
            """
            SELECT * FROM recommendation_batch_items
            WHERE batch_id = ? ORDER BY position ASC
            """,
            (batch_id,),
        ).fetchall()
        return {
            "id": str(row["id"]),
            "user_id": str(row["user_id"]),
            "kind": str(row["kind"]),
            "scenario": str(row["scenario"]),
            "current_track_id": row["current_track_id"],
            "profile": json.loads(row["profile_json"] or "{}"),
            "constraints": json.loads(row["constraints_json"] or "{}"),
            "created_at": str(row["created_at"]),
            "items": [
                {
                    "position": int(item["position"]),
                    "track_id": str(item["track_id"]),
                    "score": float(item["score"]),
                    "reasons": json.loads(item["reasons_json"] or "[]"),
                    "track": json.loads(item["track_json"]),
                }
                for item in items
            ],
        }
    finally:
        conn.close()
