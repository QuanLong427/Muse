"""Durable correlation store for browser-side player actions.

Agent tools run on the backend, while playback is executed by the browser.
This store bridges that boundary: tools register an issued command, the browser
acknowledges the exact ``action_id``, and protocol validation can then describe
the observed result instead of guessing from a dispatch event.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from config import PROJECT_ROOT


_DB_DIR = PROJECT_ROOT / "memory" / "data"
_DB_PATH = _DB_DIR / "player-actions.db"
_TERMINAL_STATUSES = {"succeeded", "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn() -> sqlite3.Connection:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_player_action_db() -> None:
    conn = _get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS player_actions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                action TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'issued',
                result_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                acknowledged_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_player_actions_session_time
            ON player_actions(user_id, session_id, created_at DESC);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_action(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "session_id": str(row["session_id"]),
        "action": str(row["action"]),
        "payload": json.loads(row["payload_json"] or "{}"),
        "status": str(row["status"]),
        "result": json.loads(row["result_json"] or "{}"),
        "created_at": str(row["created_at"]),
        "acknowledged_at": row["acknowledged_at"],
    }


def issue_player_action(
    *,
    user_id: str,
    session_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Register one immutable browser command and return its correlation id."""
    action = str(payload.get("action") or "").strip()
    if payload.get("target") != "player" or not action:
        raise ValueError("player action requires target=player and action")

    init_player_action_db()
    action_id = str(uuid4())
    correlated_payload = {**payload, "action_id": action_id}
    created_at = _now()
    conn = _get_conn()
    try:
        conn.execute(
            """
            INSERT INTO player_actions(
                id, user_id, session_id, action, payload_json,
                status, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, 'issued', '{}', ?)
            """,
            (
                action_id,
                user_id.strip() or "local",
                session_id.strip() or "default",
                action,
                json.dumps(correlated_payload, ensure_ascii=False),
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "id": action_id,
        "status": "issued",
        "payload": correlated_payload,
        "created_at": created_at,
    }


def acknowledge_player_action(
    action_id: str,
    *,
    user_id: str,
    session_id: str,
    status: Literal["succeeded", "failed"],
    result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Record the browser result once; repeated identical ACKs are idempotent."""
    if status not in _TERMINAL_STATUSES:
        raise ValueError("invalid player action status")
    init_player_action_db()
    normalized_user = user_id.strip() or "local"
    normalized_session = session_id.strip() or "default"
    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT * FROM player_actions
            WHERE id = ? AND user_id = ? AND session_id = ?
            """,
            (action_id, normalized_user, normalized_session),
        ).fetchone()
        if row is None:
            return None
        if str(row["status"]) == "issued":
            conn.execute(
                """
                UPDATE player_actions
                SET status = ?, result_json = ?, acknowledged_at = ?
                WHERE id = ? AND status = 'issued'
                """,
                (
                    status,
                    json.dumps(result or {}, ensure_ascii=False),
                    _now(),
                    action_id,
                ),
            )
            conn.commit()
        updated = conn.execute(
            "SELECT * FROM player_actions WHERE id = ?", (action_id,)
        ).fetchone()
        return _row_to_action(updated) if updated is not None else None
    finally:
        conn.close()


def get_player_action(
    action_id: str,
    *,
    user_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    init_player_action_db()
    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT * FROM player_actions
            WHERE id = ? AND user_id = ? AND session_id = ?
            """,
            (
                action_id,
                user_id.strip() or "local",
                session_id.strip() or "default",
            ),
        ).fetchone()
        return _row_to_action(row) if row is not None else None
    finally:
        conn.close()


def wait_for_player_actions(
    action_ids: list[str],
    *,
    user_id: str,
    session_id: str,
    timeout_seconds: float = 6.0,
) -> dict[str, dict[str, Any]]:
    """Wait briefly for browser ACKs and return every action found."""
    pending = {action_id for action_id in action_ids if action_id}
    results: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while pending:
        for action_id in list(pending):
            action = get_player_action(
                action_id,
                user_id=user_id,
                session_id=session_id,
            )
            if action is None:
                pending.remove(action_id)
                continue
            results[action_id] = action
            if action["status"] in _TERMINAL_STATUSES:
                pending.remove(action_id)
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    return results
