import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import memory_manager, memory_store


def _isolate_memory(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    template = tmp_path / "template.md"
    template.write_text(
        "# 用户画像\n\n## 全局基准\n\n- 保留全局规则\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(memory_store, "MEMORY_DATA_DIR", data_dir)
    monkeypatch.setattr(memory_store, "MEMORY_DB_PATH", data_dir / "memory.db")
    monkeypatch.setattr(memory_manager, "DATA_DIR", data_dir)
    monkeypatch.setattr(memory_manager, "PROFILE_FILE", data_dir / "user_profile.md")
    monkeypatch.setattr(memory_manager, "HISTORY_FILE", data_dir / "history.jsonl")
    monkeypatch.setattr(memory_manager, "TEMPLATE_PROFILE", template)
    memory_store.init_memory_db()
    return data_dir


def test_profile_projection_separates_global_and_scenarios(monkeypatch, tmp_path):
    data_dir = _isolate_memory(monkeypatch, tmp_path)
    profile = data_dir / "user_profile.md"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        """# 用户画像

## 全局基准

- 保留全局规则

## 场景:跑步

- 手写规则：优先高节奏

## 场景:睡觉

- 手写规则：控制音量
""",
        encoding="utf-8",
    )
    memory_store.upsert_memory_item(
        user_id="local",
        kind="preference",
        scenario="全局",
        memory_key="global:avoid-cover",
        directive="避免低质量翻唱",
        confidence=1.0,
        source_message_ids=[],
    )
    memory_store.upsert_memory_item(
        user_id="local",
        kind="preference",
        scenario="跑步",
        memory_key="running:fast",
        directive="跑步时优先高节奏歌曲",
        confidence=1.0,
        source_message_ids=[],
    )

    memory_manager.sync_profile_projection("local")

    global_profile = profile.read_text(encoding="utf-8")
    running_profile = memory_manager.read_scenario_profile("跑步", "local")
    sleeping_profile = memory_manager.read_scenario_profile("睡觉", "local")
    assert "场景:跑步" not in global_profile
    assert "避免低质量翻唱" in global_profile
    assert "跑步时优先高节奏歌曲" not in global_profile
    assert "手写规则：优先高节奏" in running_profile
    assert "running:fast" in running_profile
    assert "避免低质量翻唱" not in running_profile
    assert "手写规则：控制音量" in sleeping_profile


def test_scenario_filename_is_bounded_to_profile_directory(monkeypatch, tmp_path):
    data_dir = _isolate_memory(monkeypatch, tmp_path)
    path = memory_manager._scenario_profile_path("../../驾驶/夜间", "local")

    assert path.parent == data_dir / "scenarios"
    assert ".." not in path.name


def test_forgetting_a_scenario_removes_active_preferences(monkeypatch, tmp_path):
    _isolate_memory(monkeypatch, tmp_path)
    memory_store.upsert_memory_item(
        user_id="local",
        kind="preference",
        scenario="跑步",
        memory_key="running:fast",
        directive="跑步时优先高节奏歌曲",
        confidence=1.0,
        source_message_ids=[],
    )

    count = memory_store.forget_memory_scenario(user_id="local", scenario="跑步")

    assert count == 1
    assert memory_store.list_memory_items("local", scenario="跑步") == []


def test_structured_context_selects_relevant_and_mandatory_memories(monkeypatch, tmp_path):
    _isolate_memory(monkeypatch, tmp_path)
    for kind, scenario, key, directive in [
        ("avoidance", "全局", "global:no-cover", "不要推荐低质量翻唱"),
        ("preference", "跑步", "running:rock", "跑步时优先推荐摇滚"),
        ("preference", "睡觉", "sleep:piano", "睡觉时优先推荐钢琴曲"),
    ]:
        memory_store.upsert_memory_item(
            user_id="local",
            kind=kind,
            scenario=scenario,
            memory_key=key,
            directive=directive,
            confidence=1.0,
            source_message_ids=[],
        )

    context = memory_manager.get_structured_memory_context(
        "local", "跑步", "推荐一个跑步摇滚歌单"
    )

    assert "不要推荐低质量翻唱" in context
    assert "跑步时优先推荐摇滚" in context
    assert "睡觉时优先推荐钢琴曲" not in context


def test_trusted_scene_preference_generates_only_its_own_projection(monkeypatch, tmp_path):
    data_dir = _isolate_memory(monkeypatch, tmp_path)
    session = memory_store.ensure_session("s", "local", "跑步")
    source_id = memory_store.add_message(session_id=session, user_id="local", role="user", content="记住：跑步时优先摇滚", scenario="跑步")
    result = memory_store.stage_candidate(user_id="local", kind="preference", scenario="跑步", memory_key="genre:摇滚", directive="跑步时优先摇滚", confidence=1.0, evidence_type="explicit_preference", source_message_ids=[source_id])
    assert result["status"] == "promoted"
    memory_manager.sync_profile_projection("local")
    assert "跑步时优先摇滚" in memory_manager.read_scenario_profile("跑步", "local")
    assert "跑步时优先摇滚" not in memory_manager.read_profile("local")
    assert memory_manager.read_scenario_profile("睡觉", "local") == ""
    assert len(list((data_dir / "scenarios").glob("*.md"))) == 1
