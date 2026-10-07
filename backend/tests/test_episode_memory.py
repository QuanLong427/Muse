import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import memory_manager, memory_store
from services.episode_memory import archive_turn_episode
from services.memory_manager import get_relevant_episode_context


def test_interrupted_playlist_with_committed_writes_is_partial(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    session_id = memory_store.ensure_session("s", "u", "默认")
    episode = archive_turn_episode(user_id="u", session_id=session_id, scenario="默认", user_message="加入10首歌",
        source_message_ids=[], tool_events=[{"phase": "result", "name": "add_track_to_music_playlist",
            "content": json.dumps({"status": "added", "playlist": {"id": "p", "items": [{}]}})}],
        final_text="已加入1首，剩余未完成", error="execution_budget_exhausted")
    assert episode["result_status"] == "partial"
    assert episode["result_summary"] == "已加入1首，剩余未完成"


def _isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_store, "MEMORY_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory_store, "MEMORY_DB_PATH", tmp_path / "memory.db")
    memory_store.init_memory_db()


def test_episode_search_is_attributable_and_scenario_aware(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    session_id = memory_store.ensure_session("session-a", "user-a", "跑步")
    message_id = memory_store.add_message(
        session_id=session_id,
        user_id="user-a",
        role="user",
        content="创建一个夜跑歌单，不要慢歌",
        scenario="跑步",
    )
    created = memory_store.create_memory_episode(
        user_id="user-a",
        session_id=session_id,
        scenario="跑步",
        episode_type="playlist",
        goal="创建一个夜跑歌单，不要慢歌",
        constraints=["不要慢歌"],
        action_summary="调用工具：create_playlist",
        result_status="success",
        result_summary="已创建夜跑歌单",
        source_message_ids=[message_id, 99999],
        entity_refs={"playlist_id": ["playlist-1"]},
        importance=0.8,
    )

    results = memory_store.search_memory_episodes(
        "再创建一个夜跑歌单",
        user_id="user-a",
        scenario="跑步",
    )

    assert results[0]["id"] == created["id"]
    assert results[0]["source_message_ids"] == [message_id]
    assert results[0]["entity_refs"]["playlist_id"] == ["playlist-1"]
    assert results[0]["retrieval_score"] >= 0.3


def test_implicit_episode_recall_is_conditional(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    session_id = memory_store.ensure_session("session-a", "user-a", "跑步")
    message_id = memory_store.add_message(
        session_id=session_id,
        user_id="user-a",
        role="user",
        content="夜跑歌单不要慢歌",
        scenario="跑步",
    )
    memory_store.create_memory_episode(
        user_id="user-a",
        session_id=session_id,
        scenario="跑步",
        episode_type="playlist",
        goal="夜跑歌单不要慢歌",
        constraints=["不要慢歌"],
        action_summary="调用工具：create_playlist",
        result_status="success",
        result_summary="歌单创建成功",
        source_message_ids=[message_id],
        importance=0.8,
    )

    implicit = get_relevant_episode_context(
        "user-a", "跑步", "帮我重新做个夜跑歌单"
    )
    simple_control = get_relevant_episode_context("user-a", "跑步", "播放下一首")
    unrelated = get_relevant_episode_context("user-a", "跑步", "推荐一些古典钢琴")

    assert "不要慢歌" in implicit["text"]
    assert len(implicit["episode_ids"]) == 1
    assert simple_control == {"text": "", "episode_ids": [], "episodes": []}
    assert unrelated == {"text": "", "episode_ids": [], "episodes": []}


def test_archive_turn_episode_records_tools_outcome_and_entities(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    session_id = memory_store.ensure_session("session-a", "user-a", "默认")
    message_id = memory_store.add_message(
        session_id=session_id,
        user_id="user-a",
        role="user",
        content="把这个版本加入歌单 BV1ab411c7xy",
        scenario="默认",
    )

    episode = archive_turn_episode(
        user_id="user-a",
        session_id=session_id,
        scenario="默认",
        user_message="把这个版本加入歌单 BV1ab411c7xy",
        source_message_ids=[message_id],
        tool_events=[
            {
                "phase": "call",
                "name": "create_playlist",
                "input": {"playlist_id": "playlist-2"},
            },
            {
                "phase": "result",
                "name": "create_playlist",
                "content": '{"status":"success","bvid":"BV1ab411c7xy"}',
            },
        ],
        final_text="已加入歌单",
        retrieved_episode_ids=["older-episode"],
    )

    assert episode["episode_type"] == "playlist"
    assert episode["result_status"] == "success"
    assert "create_playlist" in episode["action_summary"]
    assert episode["entity_refs"]["bvid"] == ["BV1ab411c7xy"]
    assert episode["entity_refs"]["playlist_id"] == ["playlist-2"]
    assert episode["context"]["retrieved_episode_ids"] == ["older-episode"]


def test_capability_question_is_not_reclassified_by_stray_tool_calls(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    session_id = memory_store.ensure_session("session-a", "user-a", "默认")

    episode = archive_turn_episode(
        user_id="user-a",
        session_id=session_id,
        scenario="默认",
        user_message="你可以做哪些事情",
        source_message_ids=[],
        tool_events=[{"phase": "call", "name": "bili_search", "input": {}}],
        final_text="协议阻断",
        error="agent_protocol_blocked:unobserved_action_claim",
    )

    assert episode["episode_type"] == "capability"
    assert episode["result_status"] == "failed"


def test_failed_episode_is_not_recalled(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    session_id = memory_store.ensure_session("session-a", "user-a", "默认")
    memory_store.create_memory_episode(
        user_id="user-a",
        session_id=session_id,
        scenario="默认",
        episode_type="music_search",
        goal="搜索最长的电影",
        result_status="failed",
        result_summary="协议阻断",
        importance=1.0,
    )

    assert (
        memory_store.search_memory_episodes(
            "搜索最长的电影",
            user_id="user-a",
            scenario="默认",
        )
        == []
    )


def test_non_memory_intent_skips_episode_search(monkeypatch):
    def unexpected_search(*args, **kwargs):
        raise AssertionError("non-memory intent must not query episodic storage")

    monkeypatch.setattr(memory_manager, "search_memory_episodes", unexpected_search)

    assert memory_manager.get_relevant_episode_context(
        "user-a",
        "默认",
        "你可以做哪些事情",
    ) == {"text": "", "episode_ids": [], "episodes": []}
