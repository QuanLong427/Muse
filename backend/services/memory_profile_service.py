"""Scene profiles derived from evidence, never fabricated defaults."""
from services.memory_store import list_memory_items
from services.preference_service import build_recent_preference_profile


def get_scene_profile(user_id: str, scenario: str = "默认") -> dict:
    scenario = scenario.strip() or "默认"
    items = list_memory_items(user_id, scenario=scenario, include_global=True, limit=200)
    recent = build_recent_preference_profile(user_id, scenario=scenario)
    has_scene_data = bool(any(item["scenario"] == scenario for item in items) or any(window["tracks"] for window in recent["windows"]))
    has_data = bool(items or has_scene_data)
    return {"user_id": user_id, "scenario": scenario,
        "status": "available" if has_data else "insufficient_data",
        "scenario_status": "available" if has_scene_data else "insufficient_data",
        "global_memories": [item for item in items if item["scenario"] == "全局"],
        "scenario_memories": [item for item in items if item["scenario"] == scenario],
        "recent_preferences": recent,
        "notice": "" if has_data else "当前场景尚无足够的真实偏好证据；不会自动套用其他场景的行为或生成偏好。"}
