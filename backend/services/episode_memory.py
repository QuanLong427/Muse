"""Turn-level episodic memory for Musicer's agent.

Episodes are compact records of observable interaction facts.  They never
store hidden model reasoning: only the user's goal, supplied context, tools
that ran, returned outcomes and attributable entity identifiers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

from services.memory_store import create_memory_episode
from services.episode_evidence import compact_player_state, entity_refs, turn_evidence, episode_outcome


_CONSTRAINT_PATTERN = re.compile(
    r"[^，。！？!?\n]*(?:不要|不能|必须|只要|仅|优先|避免|排除|保留|确认后)[^，。！？!?\n]*"
)


def _entity_refs(*values: Any) -> dict[str, list[str]]:
    return entity_refs(list(values))


def _classify_episode(user_message: str, tool_names: Iterable[str]) -> str:
    names = set(tool_names)
    text = user_message.lower()
    if re.search(
        r"你(?:可以|能|会)做(?:什么|哪些)|你有(?:什么|哪些)(?:功能|能力)|支持(?:什么|哪些)功能",
        text,
    ):
        return "capability"
    if re.search(r"不是|不对|纠正|改成|记错|说错", text):
        return "correction"
    if names & {"convert_video", "download_music", "download_audio"} or "下载" in text:
        return "download"
    if "歌单" in text or any("playlist" in name for name in names):
        return "playlist"
    if "recommend_music" in names or re.search(r"推荐|radio|电台", text):
        return "recommendation"
    if names & {"local_search", "bili_search", "web_search"} or re.search(
        r"搜索|查找|找歌|哪个版本|翻唱|原唱|live|remix", text
    ):
        return "music_search"
    if names & {"control_player", "get_player_state"} or re.search(
        r"播放|暂停|下一首|上一首|音量|循环|随机", text
    ):
        return "playback_control"
    if "知识库" in text or "wiki" in text or "execute_skill_script:llm-wiki" in names:
        return "knowledge"
    if names & {"search_memory", "remember_preference", "forget_preference"}:
        return "memory_management"
    return "conversation"


def _compact_player_state(player_state: dict[str, Any] | None) -> dict[str, Any]:
    return compact_player_state(player_state)


def archive_turn_episode(
    *,
    user_id: str,
    session_id: str,
    scenario: str,
    user_message: str,
    source_message_ids: Iterable[int],
    tool_events: list[dict[str, Any]],
    final_text: str,
    player_state: dict[str, Any] | None = None,
    retrieved_episode_ids: Iterable[str] = (),
    client_action_results: dict[str, dict] | None = None,
    selected_tracks: list[dict] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Derive and persist one episode after an agent turn finishes."""
    tool_names = [
        str(event.get("name"))
        for event in tool_events
        if event.get("name") and event.get("phase") == "call"
    ]
    unique_tools = list(dict.fromkeys(tool_names))
    evidence = turn_evidence(tool_events, client_action_results, selected_tracks)
    result_status = episode_outcome(evidence, interrupted=bool(error), had_calls=bool(tool_names))
    # The assistant's prose remains attributable, but is not the fact source.
    result_summary = (final_text or ("操作已中断" if error else ""))[:4000]

    classified_names = [*unique_tools, *[
        "execute_skill_script:" + str(event.get("input", {}).get("skill_name", ""))
        for event in tool_events if event.get("phase") == "call" and event.get("name") == "execute_skill_script"
        and isinstance(event.get("input"), dict)]]
    episode_type = _classify_episode(user_message, classified_names)
    importance_by_type = {
        "correction": 0.9,
        "playlist": 0.8,
        "download": 0.75,
        "music_search": 0.6,
        "recommendation": 0.7,
        "knowledge": 0.6,
        "memory_management": 0.65,
        "playback_control": 0.3,
        "conversation": 0.2,
        "capability": 0.1,
    }
    constraints = [
        match.group(0).strip()
        for match in _CONSTRAINT_PATTERN.finditer(user_message)
        if match.group(0).strip()
    ]
    entities = _entity_refs(user_message, tool_events, selected_tracks or [])
    context = {
        "scenario": scenario or "默认",
        "player": _compact_player_state(player_state),
        "retrieved_episode_ids": list(dict.fromkeys(retrieved_episode_ids))[:10],
        "evidence_version": 2,
        **evidence,
    }
    action_summary = (
        "调用工具：" + "、".join(unique_tools)
        if unique_tools
        else "未调用工具，直接回复"
    )
    episode = create_memory_episode(
        user_id=user_id,
        session_id=session_id,
        scenario=scenario,
        episode_type=episode_type,
        goal=user_message,
        context=context,
        constraints=constraints,
        action_summary=action_summary,
        result_status=result_status,
        result_summary=result_summary,
        source_message_ids=source_message_ids,
        entity_refs=entities,
        importance=importance_by_type[episode_type],
        confidence=1.0 if result_status == "success" else 0.75,
    )
    # Memory linkage must never change a successful business operation into a failure.
    from services.memory_task_service import record_task_episode
    try:
        episode["task_ids"] = record_task_episode(episode, tool_events)
    except Exception:
        logging.getLogger(__name__).exception("[memory] task linkage will need inspection")
    return episode


def serialize_tool_result(value: Any, limit: int = 4000) -> str:
    """Produce a stable bounded representation for episode extraction."""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except (TypeError, ValueError):
        return str(value)[:limit]
