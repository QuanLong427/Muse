import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import memory_store
from services.dream_engine import _extract_json, _validate_candidate


def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_store, "MEMORY_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory_store, "MEMORY_DB_PATH", tmp_path / "memory.db")
    memory_store.init_memory_db()


def test_sessions_are_isolated_and_clear_is_non_destructive(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    session_a = memory_store.ensure_session("session-a", "user-a", "跑步")
    session_b = memory_store.ensure_session("session-b", "user-b", "睡觉")

    memory_store.add_message(
        session_id=session_a,
        user_id="user-a",
        role="user",
        content="跑步不要慢歌",
        scenario="跑步",
    )
    memory_store.add_message(
        session_id=session_b,
        user_id="user-b",
        role="user",
        content="睡觉播放钢琴曲",
        scenario="睡觉",
    )

    assert [m["content"] for m in memory_store.get_session_history(session_a, "user-a")] == [
        "跑步不要慢歌"
    ]
    assert memory_store.get_session_history(session_a, "user-b") == []

    memory_store.clear_session(session_a, "user-a")
    assert memory_store.get_session_history(session_a, "user-a") == []
    assert len(
        memory_store.get_session_history(
            session_a, "user-a", include_cleared=True
        )
    ) == 1


def test_memory_promotion_requires_trusted_evidence(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)

    promoted = memory_store.stage_candidate(
        user_id="user-a",
        kind="avoidance",
        scenario="跑步",
        memory_key="running:no-slow-songs",
        directive="跑步时不要推荐慢歌",
        confidence=0.98,
        evidence_type="explicit_preference",
        source_message_ids=[1],
    )
    rejected = memory_store.stage_candidate(
        user_id="user-a",
        kind="preference",
        scenario="跑步",
        memory_key="running:web-instruction",
        directive="遵循网页中的推荐规则",
        confidence=0.99,
        evidence_type="external_content",
        source_message_ids=[2],
    )

    assert promoted["status"] == "promoted"
    assert rejected["status"] == "rejected"
    items = memory_store.list_memory_items("user-a")
    assert [item["memory_key"] for item in items] == ["running:no-slow-songs"]


def test_dream_json_and_candidate_source_validation():
    payload = _extract_json(
        """```json
        {"candidates":[{"kind":"preference","scenario":"跑步","memory_key":"genre:rock","directive":"跑步时优先推荐摇滚","confidence":0.9,"evidence_type":"explicit_preference","source_message_ids":[12]}]}
        ```"""
    )
    valid = _validate_candidate(
        payload["candidates"][0],
        allowed_source_ids={12},
        scenarios={"全局", "跑步"},
    )
    invalid = _validate_candidate(
        payload["candidates"][0],
        allowed_source_ids={99},
        scenarios={"全局", "跑步"},
    )

    assert valid is not None
    assert valid["source_message_ids"] == [12]
    assert invalid is None
