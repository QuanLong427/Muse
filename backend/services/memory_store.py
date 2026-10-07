"""SQLite-backed memory store for conversations and durable user memories.

The store intentionally separates three concerns:

* sessions/messages: attributable raw interaction events and short-term recall;
* memory_episodes: structured past interactions used as episodic memory;
* memory_candidates: auditable proposals produced by Dream;
* memory_items: small, curated directives that may be injected into prompts.

All public functions open their own connection.  This keeps FastAPI requests and
the background Dream worker from sharing sqlite connection objects across
threads while rollback journals and transactions serialize writes safely.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from config import PROJECT_ROOT
from services.sqlite_runtime import connect_database


MEMORY_DATA_DIR = PROJECT_ROOT / "memory" / "data"
MEMORY_DB_PATH = MEMORY_DATA_DIR / "memory.db"
DEFAULT_USER_ID = "local"
DEFAULT_SESSION_ID = "default"

_init_lock = threading.Lock()
_initialized_path: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _connect() -> sqlite3.Connection:
    MEMORY_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return connect_database(MEMORY_DB_PATH)


def check_memory_health() -> dict:
    connection = _connect()
    try:
        connection.execute("SELECT id FROM memory_messages LIMIT 1").fetchone()
        return {"status": "ok", "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0]}
    finally:
        connection.close()


def init_memory_db() -> None:
    """Create or upgrade the memory schema."""
    global _initialized_path
    resolved_path = str(MEMORY_DB_PATH.resolve())
    if _initialized_path == resolved_path and MEMORY_DB_PATH.exists():
        return
    with _init_lock:
        if _initialized_path == resolved_path and MEMORY_DB_PATH.exists():
            return
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_users (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    scenario TEXT NOT NULL DEFAULT '默认',
                    title TEXT NOT NULL DEFAULT '',
                    cleared_before_id INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES memory_users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memory_sessions_user_updated
                    ON memory_sessions(user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS memory_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('user', 'agent', 'tool', 'system')),
                    content TEXT NOT NULL,
                    scenario TEXT NOT NULL DEFAULT '默认',
                    summary TEXT NOT NULL DEFAULT '',
                    intent TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    dream_processed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES memory_sessions(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES memory_users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memory_messages_session_id
                    ON memory_messages(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_memory_messages_user_dream
                    ON memory_messages(user_id, dream_processed, id);

                CREATE TABLE IF NOT EXISTS memory_episodes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    scenario TEXT NOT NULL DEFAULT '默认',
                    episode_type TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    constraints_json TEXT NOT NULL DEFAULT '[]',
                    action_summary TEXT NOT NULL DEFAULT '',
                    result_status TEXT NOT NULL,
                    result_summary TEXT NOT NULL DEFAULT '',
                    user_feedback TEXT NOT NULL DEFAULT '',
                    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
                    entity_refs_json TEXT NOT NULL DEFAULT '{}',
                    search_text TEXT NOT NULL DEFAULT '',
                    importance REAL NOT NULL DEFAULT 0.5,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES memory_users(id) ON DELETE CASCADE,
                    FOREIGN KEY(session_id) REFERENCES memory_sessions(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memory_episodes_user_created
                    ON memory_episodes(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_memory_episodes_context
                    ON memory_episodes(user_id, scenario, episode_type, created_at DESC);

                CREATE TABLE IF NOT EXISTS memory_candidates (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    scenario TEXT NOT NULL DEFAULT '默认',
                    memory_key TEXT NOT NULL,
                    directive TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_type TEXT NOT NULL,
                    source_message_ids_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES memory_users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_memory_candidates_key
                    ON memory_candidates(user_id, kind, scenario, memory_key, status);

                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    scenario TEXT NOT NULL DEFAULT '默认',
                    memory_key TEXT NOT NULL,
                    directive TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    evidence_count INTEGER NOT NULL DEFAULT 1,
                    source_message_ids_json TEXT NOT NULL,
                    first_observed_at TEXT NOT NULL,
                    last_observed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES memory_users(id) ON DELETE CASCADE,
                    UNIQUE(user_id, kind, scenario, memory_key)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_items_context
                    ON memory_items(user_id, status, scenario, updated_at DESC);

                CREATE TABLE IF NOT EXISTS memory_dream_runs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    processed_count INTEGER NOT NULL DEFAULT 0,
                    promoted_count INTEGER NOT NULL DEFAULT 0,
                    pending_count INTEGER NOT NULL DEFAULT 0,
                    rejected_count INTEGER NOT NULL DEFAULT 0,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES memory_users(id) ON DELETE CASCADE
                );
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO memory_meta(key, value) VALUES('schema_version', '3')"
            )
            conn.commit()
            _initialized_path = resolved_path
        finally:
            conn.close()


def ensure_user(user_id: str = DEFAULT_USER_ID) -> str:
    init_memory_db()
    normalized = (user_id or DEFAULT_USER_ID).strip()[:128] or DEFAULT_USER_ID
    now = _now()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO memory_users(id, created_at, updated_at) VALUES(?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (normalized, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return normalized


def ensure_session(
    session_id: str | None,
    user_id: str = DEFAULT_USER_ID,
    scenario: str = "默认",
) -> str:
    user_id = ensure_user(user_id)
    normalized = (session_id or "").strip()[:160] or str(uuid.uuid4())
    now = _now()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT user_id FROM memory_sessions WHERE id = ?", (normalized,)
        ).fetchone()
        if row and row["user_id"] != user_id:
            # Never allow a caller to attach to another user's session merely by
            # guessing an id. Create a fresh opaque id instead.
            normalized = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO memory_sessions(
                id, user_id, scenario, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                scenario = excluded.scenario,
                updated_at = excluded.updated_at
            """,
            (normalized, user_id, scenario or "默认", now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return normalized


def add_message(
    *,
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    scenario: str = "默认",
    summary: str = "",
    intent: str = "",
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    dream_processed: bool = False,
) -> int:
    session_id = ensure_session(session_id, user_id, scenario)
    user_id = ensure_user(user_id)
    if role not in {"user", "agent", "tool", "system"}:
        raise ValueError(f"unsupported memory role: {role}")
    now = created_at or _now()
    conn = _connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO memory_messages(
                session_id, user_id, role, content, scenario, summary, intent,
                metadata_json, dream_processed, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                user_id,
                role,
                content,
                scenario or "默认",
                summary,
                intent,
                _json(metadata or {}),
                1 if dream_processed else 0,
                now,
            ),
        )
        conn.execute(
            "UPDATE memory_sessions SET updated_at = ?, scenario = ? WHERE id = ?",
            (now, scenario or "默认", session_id),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _message_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "user_id": row["user_id"],
        "role": row["role"],
        "content": row["content"],
        "scenario": row["scenario"],
        "summary": row["summary"],
        "intent": row["intent"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "timestamp": row["created_at"],
        "dream_processed": bool(row["dream_processed"]),
    }


def get_recent_messages(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    limit: int = 16,
    *,
    include_cleared: bool = False,
) -> list[dict[str, Any]]:
    init_memory_db()
    safe_limit = max(1, min(int(limit), 200))
    conn = _connect()
    try:
        session = conn.execute(
            "SELECT cleared_before_id FROM memory_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        if not session:
            return []
        cleared = 0 if include_cleared else int(session["cleared_before_id"] or 0)
        rows = conn.execute(
            """
            SELECT * FROM memory_messages
            WHERE session_id = ? AND user_id = ? AND id > ?
              AND role IN ('user', 'agent')
            ORDER BY id DESC LIMIT ?
            """,
            (session_id, user_id, cleared, safe_limit),
        ).fetchall()
        return [_message_dict(row) for row in reversed(rows)]
    finally:
        conn.close()


def get_session_history(
    session_id: str,
    user_id: str = DEFAULT_USER_ID,
    *,
    include_cleared: bool = False,
    limit: int = 500,
) -> list[dict[str, Any]]:
    init_memory_db()
    safe_limit = max(1, min(int(limit), 5000))
    conn = _connect()
    try:
        session = conn.execute(
            "SELECT cleared_before_id FROM memory_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        if not session:
            return []
        cleared = 0 if include_cleared else int(session["cleared_before_id"] or 0)
        rows = conn.execute(
            """
            SELECT * FROM memory_messages
            WHERE session_id = ? AND user_id = ? AND id > ?
              AND role IN ('user', 'agent')
            ORDER BY id ASC LIMIT ?
            """,
            (session_id, user_id, cleared, safe_limit),
        ).fetchall()
        return [_message_dict(row) for row in rows]
    finally:
        conn.close()


def clear_session(session_id: str, user_id: str = DEFAULT_USER_ID) -> None:
    init_memory_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS max_id FROM memory_messages WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        conn.execute(
            "UPDATE memory_sessions SET cleared_before_id = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (int(row["max_id"]), _now(), session_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def search_messages(
    query: str,
    user_id: str = DEFAULT_USER_ID,
    *,
    scenario: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Search actual messages.

    LIKE is deliberately used for the first implementation because SQLite's
    default FTS tokenizer performs poorly for unsegmented Chinese queries.  The
    schema can gain a trigram/vector index later without changing this API.
    """
    init_memory_db()
    query = query.strip()
    if not query:
        return []
    safe_limit = max(1, min(int(limit), 20))
    conn = _connect()
    try:
        sql = """
            SELECT * FROM memory_messages
            WHERE user_id = ? AND role IN ('user', 'agent') AND content LIKE ?
        """
        params: list[Any] = [user_id, f"%{query}%"]
        if scenario:
            sql += " AND scenario = ?"
            params.append(scenario)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(safe_limit)
        rows = conn.execute(sql, params).fetchall()
        return [_message_dict(row) for row in rows]
    finally:
        conn.close()


def _episode_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["context"] = json.loads(item.pop("context_json") or "{}")
    item["constraints"] = json.loads(item.pop("constraints_json") or "[]")
    item["source_message_ids"] = json.loads(
        item.pop("source_message_ids_json") or "[]"
    )
    item["entity_refs"] = json.loads(item.pop("entity_refs_json") or "{}")
    return item


def _bounded_score(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def create_memory_episode(
    *,
    user_id: str,
    session_id: str,
    scenario: str,
    episode_type: str,
    goal: str,
    context: dict[str, Any] | None = None,
    constraints: Iterable[str] = (),
    action_summary: str = "",
    result_status: str = "success",
    result_summary: str = "",
    user_feedback: str = "",
    source_message_ids: Iterable[int] = (),
    entity_refs: dict[str, Any] | None = None,
    importance: float = 0.5,
    confidence: float = 1.0,
) -> dict[str, Any]:
    """Store a compact, attributable episode derived from one completed turn."""
    user_id = ensure_user(user_id)
    session_id = ensure_session(session_id, user_id, scenario)
    goal = goal.strip()[:2000]
    if not goal:
        raise ValueError("episode goal must not be empty")
    result_status = result_status.strip().lower()
    if result_status not in {"success", "partial", "failed"}:
        raise ValueError(f"unsupported episode result status: {result_status}")

    requested_sources = sorted({int(value) for value in source_message_ids})
    valid_sources: list[int] = []
    conn = _connect()
    try:
        if requested_sources:
            placeholders = ",".join("?" for _ in requested_sources)
            rows = conn.execute(
                f"""
                SELECT id FROM memory_messages
                WHERE user_id = ? AND session_id = ? AND id IN ({placeholders})
                """,
                [user_id, session_id, *requested_sources],
            ).fetchall()
            valid_sources = sorted(int(row["id"]) for row in rows)

        normalized_constraints = [
            str(value).strip()[:300]
            for value in constraints
            if str(value).strip()
        ][:20]
        normalized_entities = entity_refs or {}
        searchable_parts = [
            goal,
            action_summary,
            result_summary,
            user_feedback,
            " ".join(normalized_constraints),
            json.dumps(normalized_entities, ensure_ascii=False, default=str),
        ]
        now = _now()
        episode_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO memory_episodes(
                id, user_id, session_id, scenario, episode_type, goal,
                context_json, constraints_json, action_summary, result_status,
                result_summary, user_feedback, source_message_ids_json,
                entity_refs_json, search_text, importance, confidence,
                created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                episode_id,
                user_id,
                session_id,
                (scenario or "默认").strip()[:80],
                episode_type.strip()[:80] or "conversation",
                goal,
                _json(context or {}),
                _json(normalized_constraints),
                action_summary.strip()[:2000],
                result_status,
                result_summary.strip()[:4000],
                user_feedback.strip()[:1000],
                _json(valid_sources),
                _json(normalized_entities),
                "\n".join(part for part in searchable_parts if part).lower()[:12000],
                _bounded_score(importance),
                _bounded_score(confidence),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM memory_episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        return _episode_dict(row)
    finally:
        conn.close()


def list_memory_episodes(
    user_id: str = DEFAULT_USER_ID,
    *,
    scenario: str | None = None,
    episode_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    init_memory_db()
    sql = "SELECT * FROM memory_episodes WHERE user_id = ?"
    params: list[Any] = [user_id]
    if scenario:
        sql += " AND scenario = ?"
        params.append(scenario)
    if episode_type:
        sql += " AND episode_type = ?"
        params.append(episode_type)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    conn = _connect()
    try:
        return [_episode_dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _search_terms(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", text.lower())
    terms = set(re.findall(r"[a-z0-9_-]{2,}", normalized))
    for segment in re.findall(r"[\u3400-\u9fff]+", normalized):
        if len(segment) == 1:
            terms.add(segment)
        else:
            terms.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return terms


def _age_days(timestamp: str) -> float:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400)
    except (TypeError, ValueError):
        return 365.0


def search_memory_episodes(
    query: str,
    user_id: str = DEFAULT_USER_ID,
    *,
    scenario: str | None = None,
    limit: int = 3,
    min_score: float = 0.22,
) -> list[dict[str, Any]]:
    """Rank episodes using lexical relevance, context, recency and salience.

    Chinese bigrams are used instead of SQLite FTS so unsegmented queries work
    without an external tokenizer.  The stable API leaves room for a vector or
    hybrid index later.
    """
    init_memory_db()
    query = query.strip()
    if not query:
        return []
    query_terms = _search_terms(query)
    explicit_recall = bool(
        re.search(r"上次|之前|以前|还记得|刚才|前面|像上回|继续|历史", query)
    )
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM memory_episodes
            WHERE user_id = ? AND result_status IN ('success', 'partial')
            ORDER BY created_at DESC LIMIT 300
            """,
            (user_id,),
        ).fetchall()
        ranked: list[tuple[float, dict[str, Any]]] = []
        normalized_query = re.sub(r"\s+", "", query.lower())
        for row in rows:
            item = _episode_dict(row)
            document = str(item.get("search_text") or "").lower()
            document_terms = _search_terms(document)
            overlap = len(query_terms & document_terms) / max(len(query_terms), 1)
            if overlap == 0 and not explicit_recall:
                continue
            exact_bonus = (
                0.2
                if normalized_query
                and normalized_query in re.sub(r"\s+", "", document)
                else 0.0
            )
            scenario_bonus = 0.08 if scenario and item["scenario"] == scenario else 0.0
            recency = math.exp(-_age_days(item["created_at"]) / 45.0)
            status_bonus = 0.03 if item["result_status"] == "success" else 0.0
            score = (
                0.62 * overlap
                + exact_bonus
                + scenario_bonus
                + 0.12 * recency
                + 0.08 * float(item["importance"])
                + 0.04 * float(item["confidence"])
                + status_bonus
            )
            if explicit_recall and overlap == 0:
                score += 0.08 * recency
            if score >= min_score:
                item["retrieval_score"] = round(min(score, 1.0), 4)
                ranked.append((score, item))

        ranked.sort(key=lambda pair: (pair[0], pair[1]["created_at"]), reverse=True)
        selected = [item for _, item in ranked[: max(1, min(int(limit), 10))]]
        if selected:
            accessed_at = _now()
            conn.executemany(
                "UPDATE memory_episodes SET last_accessed_at = ? WHERE id = ?",
                [(accessed_at, item["id"]) for item in selected],
            )
            conn.commit()
        return selected
    finally:
        conn.close()


def get_pending_messages(
    user_id: str = DEFAULT_USER_ID,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_memory_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT * FROM memory_messages
            WHERE user_id = ? AND dream_processed = 0
            ORDER BY id ASC LIMIT ?
            """,
            (user_id, max(1, min(int(limit), 500))),
        ).fetchall()
        return [_message_dict(row) for row in rows]
    finally:
        conn.close()


def count_pending_messages(user_id: str = DEFAULT_USER_ID) -> int:
    init_memory_db()
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM memory_messages
            WHERE user_id = ? AND dream_processed = 0
              AND role IN ('user', 'agent')
            """,
            (user_id,),
        ).fetchone()
        return int(row["n"])
    finally:
        conn.close()


def list_users_with_pending_messages() -> list[str]:
    init_memory_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT user_id FROM memory_messages
            WHERE dream_processed = 0 ORDER BY user_id
            """
        ).fetchall()
        return [str(row["user_id"]) for row in rows]
    finally:
        conn.close()


def mark_messages_processed(message_ids: Iterable[int]) -> None:
    ids = sorted({int(value) for value in message_ids})
    if not ids:
        return
    init_memory_db()
    placeholders = ",".join("?" for _ in ids)
    conn = _connect()
    try:
        conn.execute(
            f"UPDATE memory_messages SET dream_processed = 1 WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
    finally:
        conn.close()


def list_memory_items(
    user_id: str = DEFAULT_USER_ID,
    *,
    scenario: str | None = None,
    include_global: bool = True,
    status: str = "active",
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_memory_db()
    conn = _connect()
    try:
        sql = "SELECT * FROM memory_items WHERE user_id = ? AND status = ?"
        params: list[Any] = [user_id, status]
        if scenario:
            if include_global:
                sql += " AND scenario IN (?, '全局')"
            else:
                sql += " AND scenario = ?"
            params.append(scenario)
        sql += " ORDER BY confidence DESC, updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_message_ids"] = json.loads(
                item.pop("source_message_ids_json") or "[]"
            )
            result.append(item)
        return result
    finally:
        conn.close()


def _merge_sources(existing: Iterable[int], incoming: Iterable[int]) -> list[int]:
    return sorted({int(value) for value in [*existing, *incoming]})[-50:]


def upsert_memory_item(
    *,
    user_id: str,
    kind: str,
    scenario: str,
    memory_key: str,
    directive: str,
    confidence: float,
    source_message_ids: Iterable[int] = (),
) -> dict[str, Any]:
    user_id = ensure_user(user_id)
    scenario = (scenario or "默认").strip()[:80]
    memory_key = memory_key.strip()[:160]
    directive = directive.strip()[:1000]
    if not memory_key or not directive:
        raise ValueError("memory_key and directive are required")
    confidence = max(0.0, min(float(confidence), 1.0))
    now = _now()
    conn = _connect()
    try:
        existing = conn.execute(
            """
            SELECT * FROM memory_items
            WHERE user_id = ? AND kind = ? AND scenario = ? AND memory_key = ?
            """,
            (user_id, kind, scenario, memory_key),
        ).fetchone()
        if existing:
            sources = _merge_sources(
                json.loads(existing["source_message_ids_json"] or "[]"),
                source_message_ids,
            )
            evidence_count = max(int(existing["evidence_count"]), len(sources), 1)
            conn.execute(
                """
                UPDATE memory_items SET
                    directive = ?, confidence = ?, status = 'active',
                    evidence_count = ?, source_message_ids_json = ?,
                    last_observed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    directive,
                    max(confidence, float(existing["confidence"])),
                    evidence_count,
                    _json(sources),
                    now,
                    now,
                    existing["id"],
                ),
            )
            memory_id = str(existing["id"])
        else:
            memory_id = str(uuid.uuid4())
            sources = _merge_sources([], source_message_ids)
            conn.execute(
                """
                INSERT INTO memory_items(
                    id, user_id, kind, scenario, memory_key, directive,
                    confidence, status, evidence_count,
                    source_message_ids_json, first_observed_at,
                    last_observed_at, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    user_id,
                    kind,
                    scenario,
                    memory_key,
                    directive,
                    confidence,
                    max(len(sources), 1),
                    _json(sources),
                    now,
                    now,
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return next(
        item for item in list_memory_items(user_id, limit=500) if item["id"] == memory_id
    )


def forget_memory_item(
    *,
    user_id: str,
    memory_key: str,
    scenario: str | None = None,
) -> int:
    init_memory_db()
    conn = _connect()
    try:
        sql = """
            UPDATE memory_items SET status = 'forgotten', updated_at = ?
            WHERE user_id = ? AND memory_key = ? AND status = 'active'
        """
        params: list[Any] = [_now(), user_id, memory_key]
        if scenario:
            sql += " AND scenario = ?"
            params.append(scenario)
        cursor = conn.execute(sql, params)
        conn.commit()
        return int(cursor.rowcount)
    finally:
        conn.close()


def forget_memory_scenario(
    *,
    user_id: str,
    scenario: str,
) -> int:
    """Soft-delete active durable memories when a user removes a scene."""
    init_memory_db()
    normalized = (scenario or "").strip()[:80]
    if not normalized or normalized == "全局":
        return 0
    conn = _connect()
    try:
        cursor = conn.execute(
            """
            UPDATE memory_items SET status = 'forgotten', updated_at = ?
            WHERE user_id = ? AND scenario = ? AND status = 'active'
            """,
            (_now(), user_id, normalized),
        )
        conn.commit()
        return int(cursor.rowcount)
    finally:
        conn.close()


def stage_candidate(
    *,
    user_id: str,
    kind: str,
    scenario: str,
    memory_key: str,
    directive: str,
    confidence: float,
    evidence_type: str,
    source_message_ids: Iterable[int],
) -> dict[str, Any]:
    """Persist a candidate and apply deterministic promotion gates."""
    user_id = ensure_user(user_id)
    sources = sorted({int(value) for value in source_message_ids})
    confidence = max(0.0, min(float(confidence), 1.0))
    scenario = (scenario or "默认").strip()[:80]
    memory_key = memory_key.strip()[:160]
    directive = directive.strip()[:1000]

    status = "pending"
    reason = "等待更多独立证据"
    if not sources or not memory_key or not directive:
        status, reason = "rejected", "缺少来源或必要字段"
    elif evidence_type in {"temporary_request", "agent_inference", "external_content"}:
        status, reason = "rejected", "临时、推测或外部内容不得自动晋升"
    elif evidence_type in {"explicit_preference", "explicit_correction"} and confidence >= 0.75:
        status, reason = "promoted", "用户明确表达且置信度达标"

    candidate_id = str(uuid.uuid4())
    conn = _connect()
    try:
        if status == "pending" and confidence >= 0.85:
            previous = conn.execute(
                """
                SELECT source_message_ids_json FROM memory_candidates
                WHERE user_id = ? AND kind = ? AND scenario = ?
                  AND memory_key = ? AND status IN ('pending', 'promoted')
                """,
                (user_id, kind, scenario, memory_key),
            ).fetchall()
            prior_sources: set[int] = set()
            for row in previous:
                prior_sources.update(json.loads(row["source_message_ids_json"] or "[]"))
            if prior_sources.difference(sources):
                status, reason = "promoted", "多次独立出现且置信度达标"

        conn.execute(
            """
            INSERT INTO memory_candidates(
                id, user_id, kind, scenario, memory_key, directive,
                confidence, evidence_type, source_message_ids_json,
                status, reason, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                user_id,
                kind,
                scenario,
                memory_key,
                directive,
                confidence,
                evidence_type,
                _json(sources),
                status,
                reason,
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    if status == "promoted":
        upsert_memory_item(
            user_id=user_id,
            kind=kind,
            scenario=scenario,
            memory_key=memory_key,
            directive=directive,
            confidence=confidence,
            source_message_ids=sources,
        )
    return {"id": candidate_id, "status": status, "reason": reason}


def create_dream_run(user_id: str) -> str:
    user_id = ensure_user(user_id)
    run_id = str(uuid.uuid4())
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO memory_dream_runs(id, user_id, status, started_at)
            VALUES(?, ?, 'running', ?)
            """,
            (run_id, user_id, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def finish_dream_run(
    run_id: str,
    *,
    status: str,
    processed_count: int,
    promoted_count: int = 0,
    pending_count: int = 0,
    rejected_count: int = 0,
    detail: dict[str, Any] | None = None,
) -> None:
    init_memory_db()
    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE memory_dream_runs SET
                status = ?, processed_count = ?, promoted_count = ?,
                pending_count = ?, rejected_count = ?, detail_json = ?,
                finished_at = ?
            WHERE id = ?
            """,
            (
                status,
                processed_count,
                promoted_count,
                pending_count,
                rejected_count,
                _json(detail or {}),
                _now(),
                run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def reset_user_memory(user_id: str = DEFAULT_USER_ID) -> None:
    init_memory_db()
    conn = _connect()
    try:
        conn.execute("DELETE FROM memory_users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def get_meta(key: str) -> str | None:
    init_memory_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT value FROM memory_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None
    finally:
        conn.close()


def set_meta(key: str, value: str) -> None:
    init_memory_db()
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO memory_meta(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()
