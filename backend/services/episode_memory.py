"""Turn-level episodic memory for Musicer's agent.

Episodes are compact records of observable interaction facts.  They never
store hidden model reasoning: only the user's goal, supplied context, tools
that ran, returned outcomes and attributable entity identifiers.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

from services.memory_store import create_memory_episode


_ENTITY_KEYS = {
    "bvid",
    "track_id",
    "song_id",
    "playlist_id",
    "filename",
    "local_file_path",
    "url",
}
_CONSTRAINT_PATTERN = re.compile(
    r"[^，。！？!?\n]*(?:不要|不能|必须|只要|仅|优先|避免|排除|保留|确认后)[^，。！？!?\n]*"
)
_FAILURE_PATTERN = re.compile(
    r"\b(?:error|failed|failure|unavailable|timeout)\b|失败|出错|不可用|超时",
    re.IGNORECASE,
)


def _collect_entities(value: Any, target: dict[str, set[str]]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = str(key).lower()
            if normalized_key in _ENTITY_KEYS and nested not in (None, ""):
                target.setdefault(normalized_key, set()).add(str(nested)[:1000])
            _collect_entities(nested, target)
    elif isinstance(value, (list, tuple)):
        for nested in value[:100]:
            _collect_entities(nested, target)
    elif isinstance(value, str):
        for bvid in re.findall(r"BV[0-9A-Za-z]{10}", value):
            target.setdefault("bvid", set()).add(bvid)
        for url in re.findall(r"https?://[^\s\]\[\"'<>]+", value):
            target.setdefault("url", set()).add(url.rstrip(".,;，。")[:1000])


def _entity_refs(*values: Any) -> dict[str, list[str]]:
    collected: dict[str, set[str]] = {}
    for value in values:
        _collect_entities(value, collected)
    return {key: sorted(values)[:20] for key, values in sorted(collected.items())}


def _classify_episode(user_message: str, tool_names: Iterable[str]) -> str:
    names = set(tool_names)
    text = user_message.lower()
    if re.search(r"不是|不对|纠正|改成|记错|说错", text):
        return "correction"
    if names & {"convert_video", "download_music", "download_audio"} or "下载" in text:
        return "download"
    if "歌单" in text or any("playlist" in name for name in names):
        return "playlist"
    if names & {"local_search", "bili_search", "web_search"} or re.search(
        r"搜索|查找|找歌|哪个版本|翻唱|原唱|live|remix", text
    ):
        return "music_search"
    if names & {"control_player", "get_player_state"} or re.search(
        r"播放|暂停|下一首|上一首|音量|循环|随机", text
    ):
        return "playback_control"
    if "execute_skill_script" in names or "知识库" in text or "wiki" in text:
        return "knowledge"
    if names & {"search_memory", "remember_preference", "forget_memory"}:
        return "memory_management"
    return "conversation"


def _compact_player_state(player_state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(player_state, dict):
        return {}
    allowed = (
        "current_track",
        "currentTrack",
        "playback_mode",
        "playMode",
        "is_playing",
        "isPlaying",
        "playlist_id",
        "playlistId",
    )
    return {key: player_state[key] for key in allowed if key in player_state}


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
    error: str | None = None,
) -> dict[str, Any]:
    """Derive and persist one episode after an agent turn finishes."""
    tool_names = [
        str(event.get("name"))
        for event in tool_events
        if event.get("name") and event.get("phase") == "call"
    ]
    unique_tools = list(dict.fromkeys(tool_names))
    result_fragments = [
        str(event.get("content", ""))[:1200]
        for event in tool_events
        if event.get("phase") == "result"
    ]
    observable_result = "\n".join(result_fragments)
    if error:
        result_status = "failed"
        result_summary = error[:4000]
    elif _FAILURE_PATTERN.search(observable_result):
        result_status = "partial"
        result_summary = (final_text or observable_result)[:4000]
    else:
        result_status = "success"
        result_summary = final_text[:4000]

    episode_type = _classify_episode(user_message, unique_tools)
    importance_by_type = {
        "correction": 0.9,
        "playlist": 0.8,
        "download": 0.75,
        "music_search": 0.6,
        "knowledge": 0.6,
        "memory_management": 0.65,
        "playback_control": 0.3,
        "conversation": 0.2,
    }
    constraints = [
        match.group(0).strip()
        for match in _CONSTRAINT_PATTERN.finditer(user_message)
        if match.group(0).strip()
    ]
    entities = _entity_refs(user_message, tool_events, final_text)
    context = {
        "scenario": scenario or "默认",
        "player": _compact_player_state(player_state),
        "retrieved_episode_ids": list(dict.fromkeys(retrieved_episode_ids))[:10],
    }
    action_summary = (
        "调用工具：" + "、".join(unique_tools)
        if unique_tools
        else "未调用工具，直接回复"
    )
    return create_memory_episode(
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
        confidence=1.0 if result_status != "partial" else 0.75,
    )


def serialize_tool_result(value: Any, limit: int = 4000) -> str:
    """Produce a stable bounded representation for episode extraction."""
    if isinstance(value, str):
        return value[:limit]
    try:
        return json.dumps(value, ensure_ascii=False, default=str)[:limit]
    except (TypeError, ValueError):
        return str(value)[:limit]
