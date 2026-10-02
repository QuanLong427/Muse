import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import player_action_store as store


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_DB_DIR", tmp_path)
    monkeypatch.setattr(store, "_DB_PATH", tmp_path / "player-actions.db")


def test_browser_ack_is_correlated_and_idempotent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    issued = store.issue_player_action(
        user_id="user-1",
        session_id="session-1",
        payload={"target": "player", "action": "play"},
    )

    assert issued["payload"]["action_id"] == issued["id"]
    assert store.get_player_action(
        issued["id"], user_id="user-1", session_id="session-1"
    )["status"] == "issued"

    acknowledged = store.acknowledge_player_action(
        issued["id"],
        user_id="user-1",
        session_id="session-1",
        status="succeeded",
        result={"observed": "playing"},
    )
    repeated = store.acknowledge_player_action(
        issued["id"],
        user_id="user-1",
        session_id="session-1",
        status="failed",
        result={"error": "late conflicting ack"},
    )

    assert acknowledged["status"] == "succeeded"
    assert acknowledged["result"] == {"observed": "playing"}
    assert repeated["status"] == "succeeded"


def test_ack_cannot_cross_user_or_session_boundary(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    issued = store.issue_player_action(
        user_id="user-1",
        session_id="session-1",
        payload={"target": "player", "action": "pause"},
    )

    assert (
        store.acknowledge_player_action(
            issued["id"],
            user_id="user-2",
            session_id="session-1",
            status="succeeded",
        )
        is None
    )
    assert (
        store.acknowledge_player_action(
            issued["id"],
            user_id="user-1",
            session_id="session-2",
            status="succeeded",
        )
        is None
    )


def test_wait_returns_terminal_browser_result(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    issued = store.issue_player_action(
        user_id="local",
        session_id="default",
        payload={"target": "player", "action": "set_volume", "value": 0.5},
    )
    store.acknowledge_player_action(
        issued["id"],
        user_id="local",
        session_id="default",
        status="failed",
        result={"error": "player unavailable"},
    )

    results = store.wait_for_player_actions(
        [issued["id"]],
        user_id="local",
        session_id="default",
        timeout_seconds=0,
    )

    assert results[issued["id"]]["status"] == "failed"
    assert results[issued["id"]]["result"]["error"] == "player unavailable"
