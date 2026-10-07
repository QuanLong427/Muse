from services import memory_profile_service as service


def test_scene_profile_is_evidence_only_and_does_not_leak_other_scenes(monkeypatch):
    calls = []
    def memories(user_id, **kwargs):
        calls.append((user_id, kwargs))
        return [{"scenario": "全局", "directive": "避免翻唱"}, {"scenario": "跑步", "directive": "喜欢摇滚"}] if kwargs["scenario"] == "跑步" else []
    def recent(user_id, **kwargs):
        calls.append((user_id, kwargs))
        return {"scenario": kwargs["scenario"], "windows": [{"days": 7, "tracks": []}, {"days": 30, "tracks": []}]}
    monkeypatch.setattr(service, "list_memory_items", memories)
    monkeypatch.setattr(service, "build_recent_preference_profile", recent)
    profile = service.get_scene_profile("u", "跑步")
    assert profile["status"] == "available"
    assert profile["global_memories"][0]["directive"] == "避免翻唱"
    assert profile["scenario_memories"][0]["directive"] == "喜欢摇滚"
    assert all(user == "u" and kwargs["scenario"] == "跑步" for user, kwargs in calls)
    empty = service.get_scene_profile("u", "洗澡")
    assert empty["status"] == "insufficient_data"
    assert empty["scenario_memories"] == []
    assert "不会" in empty["notice"]
