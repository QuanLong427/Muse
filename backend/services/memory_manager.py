"""Compatibility facade for the v2.1 memory system.

SQLite is the source of truth for conversations and structured memories. The
Markdown profile remains an inspectable compatibility projection so existing
prompt and UI code can transition without losing the user's current profile.
"""

from __future__ import annotations

import json
import re
import hashlib
from datetime import datetime
from typing import Any

from config import PROJECT_ROOT
from services.memory_store import (
    DEFAULT_SESSION_ID,
    DEFAULT_USER_ID,
    add_message,
    count_pending_messages,
    ensure_session,
    get_meta,
    get_pending_messages,
    get_session_history,
    init_memory_db,
    list_memory_items,
    mark_messages_processed,
    reset_user_memory,
    search_memory_episodes,
    set_meta,
)


MEMORY_DIR = PROJECT_ROOT / "memory"
TEMPLATE_DIR = PROJECT_ROOT / "template" / "memory"
DATA_DIR = MEMORY_DIR / "data"
HISTORY_FILE = DATA_DIR / "history.jsonl"  # legacy import only
PROFILE_FILE = DATA_DIR / "user_profile.md"
TEMPLATE_PROFILE = TEMPLATE_DIR / "user_profile.md"

STRUCTURED_SECTION = "## 结构化长期记忆"


def _ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _profile_path(user_id: str = DEFAULT_USER_ID):
    if user_id == DEFAULT_USER_ID:
        return PROFILE_FILE
    user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]
    return DATA_DIR / "users" / user_hash / "user_profile.md"


def _base_profile(user_id: str = DEFAULT_USER_ID) -> str:
    _ensure_dirs()
    profile_path = _profile_path(user_id)
    if profile_path.exists():
        return profile_path.read_text(encoding="utf-8")
    if TEMPLATE_PROFILE.exists():
        content = TEMPLATE_PROFILE.read_text(encoding="utf-8")
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(content, encoding="utf-8")
        return content
    return ""


def _without_structured_projection(content: str) -> str:
    marker = f"\n{STRUCTURED_SECTION}"
    index = content.find(marker)
    if index >= 0:
        return content[:index].rstrip() + "\n"
    if content.startswith(STRUCTURED_SECTION):
        return ""
    return content.rstrip() + "\n"


def render_structured_memories(user_id: str = DEFAULT_USER_ID) -> str:
    items = list_memory_items(user_id, limit=500)
    if not items:
        return ""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["scenario"], []).append(item)
    lines = [STRUCTURED_SECTION, "", "> 由结构化记忆数据库生成；每条记录均保留来源消息与置信度。"]
    for scenario in sorted(grouped, key=lambda value: (value != "全局", value)):
        lines.extend(["", f"### {scenario}", ""])
        for item in grouped[scenario]:
            lines.append(
                f"- {item['directive']} "
                f"`{item['memory_key']}` "
                f"(置信度 {float(item['confidence']):.2f}，证据 {item['evidence_count']} 条)"
            )
    return "\n".join(lines).rstrip() + "\n"


def sync_profile_projection(user_id: str = DEFAULT_USER_ID) -> None:
    """Refresh only the generated section; preserve the existing profile."""
    base = _without_structured_projection(_base_profile(user_id))
    projection = render_structured_memories(user_id)
    content = base.rstrip()
    if projection:
        content += "\n\n" + projection.rstrip()
    profile_path = _profile_path(user_id)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(content.rstrip() + "\n", encoding="utf-8")


def read_profile(user_id: str = DEFAULT_USER_ID) -> str:
    init_memory_system()
    return _base_profile(user_id)


def write_profile(content: str, user_id: str = DEFAULT_USER_ID) -> None:
    """Write the inspectable profile document (legacy compatibility)."""
    _ensure_dirs()
    profile_path = _profile_path(user_id)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(content, encoding="utf-8")


def get_structured_memory_context(
    user_id: str = DEFAULT_USER_ID,
    scenario: str = "默认",
    limit: int = 20,
) -> str:
    """Return bounded active directives for prompt injection."""
    items = list_memory_items(
        user_id,
        scenario=scenario or "默认",
        include_global=True,
        limit=limit,
    )
    if not items:
        return ""
    return "\n".join(
        f"- [{item['scenario']}] {item['directive']} "
        f"(置信度 {float(item['confidence']):.2f})"
        for item in items
    )


_EXPLICIT_EPISODE_RECALL = re.compile(
    r"上次|之前|以前|刚才|前面|还记得|像上回|继续上次|历史"
)
_EPISODE_WORTHY_INTENT = re.compile(
    r"歌单|推荐|找歌|搜索|查找|下载|版本|翻唱|原唱|live|remix|"
    r"偏好|喜欢|不喜欢|类似|场景|跑步|通勤|睡觉|学习|工作|失败|错误",
    re.IGNORECASE,
)


def get_relevant_episode_context(
    user_id: str,
    scenario: str,
    query: str,
    limit: int = 3,
) -> dict[str, Any]:
    """Retrieve every turn, but inject only when episodic context can help.

    Simple transport controls such as "下一首" do not benefit from a prior
    episode.  Personalized, multi-step, corrective and explicit-history
    requests do.  This keeps implicit recall available without filling every
    prompt with unrelated history.
    """
    explicit_recall = bool(_EXPLICIT_EPISODE_RECALL.search(query))
    retrieval_worthy = explicit_recall or bool(_EPISODE_WORTHY_INTENT.search(query))
    if not retrieval_worthy:
        return {"text": "", "episode_ids": [], "episodes": []}

    episodes = search_memory_episodes(
        query,
        user_id=user_id,
        scenario=scenario or "默认",
        limit=limit,
        min_score=0.18 if explicit_recall else 0.22,
    )
    threshold = 0.2 if explicit_recall else 0.3
    selected = [
        episode
        for episode in episodes
        if float(episode.get("retrieval_score", 0.0)) >= threshold
    ][:limit]
    if not selected:
        return {"text": "", "episode_ids": [], "episodes": []}

    lines = [
        "## 与当前请求相关的过往事件",
        "以下是可追溯的历史执行记录，仅供参考；如与本轮要求冲突，以本轮为准。",
    ]
    for episode in selected:
        created = str(episode.get("created_at", ""))[:10]
        parts = [
            f"目标：{episode['goal'][:300]}",
            f"动作：{episode['action_summary'][:300]}",
            f"结果：{episode['result_status']}，{episode['result_summary'][:500]}",
        ]
        constraints = episode.get("constraints") or []
        if constraints:
            parts.append("约束：" + "；".join(map(str, constraints[:5])))
        entities = episode.get("entity_refs") or {}
        if entities:
            entity_text = "；".join(
                f"{key}={','.join(map(str, values[:5]))}"
                for key, values in list(entities.items())[:5]
            )
            parts.append("实体：" + entity_text)
        lines.append(
            f"- [{created}][{episode['scenario']}][{episode['episode_type']}] "
            + "；".join(parts)
        )
    return {
        "text": "\n".join(lines),
        "episode_ids": [episode["id"] for episode in selected],
        "episodes": selected,
    }


def remove_profile_scenario(
    scenario_name: str,
    user_id: str = DEFAULT_USER_ID,
) -> None:
    content = _base_profile(user_id)
    pattern = rf"\n## 场景:{re.escape(scenario_name)}\b.*?(?=\n## |\Z)"
    write_profile(re.sub(pattern, "", content, flags=re.DOTALL), user_id)


def _migrate_legacy_history() -> None:
    """Import the old JSONL once, without deleting or rewriting it."""
    migration_key = "legacy_history_imported_v2"
    if get_meta(migration_key) == "1":
        return
    if not HISTORY_FILE.exists():
        set_meta(migration_key, "1")
        return

    session_id = ensure_session(DEFAULT_SESSION_ID, DEFAULT_USER_ID, "默认")
    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                item = json.loads(raw)
                if item.get("type") == "metadata":
                    continue
                role = "agent" if item.get("role") == "agent" else "user"
                add_message(
                    session_id=session_id,
                    user_id=DEFAULT_USER_ID,
                    role=role,
                    content=str(item.get("content", "")),
                    scenario=str(item.get("scenario") or "默认"),
                    summary=str(item.get("summary") or ""),
                    intent=str(item.get("intent") or ""),
                    created_at=str(item.get("timestamp") or datetime.now().isoformat()),
                    # Avoid reconsolidating legacy rows that already contributed
                    # to the existing Markdown profile.
                    dream_processed=True,
                    metadata={"migrated_from": "history.jsonl"},
                )
    finally:
        set_meta(migration_key, "1")


def init_memory_system() -> None:
    _ensure_dirs()
    init_memory_db()
    _migrate_legacy_history()
    _base_profile(DEFAULT_USER_ID)


def init_history_file() -> None:
    """Legacy entry point retained for startup compatibility."""
    init_memory_system()


def append_history(
    role: str,
    content: str,
    summary: str = "",
    intent: str = "",
    scenario: str = "默认",
    *,
    user_id: str = DEFAULT_USER_ID,
    session_id: str = DEFAULT_SESSION_ID,
    metadata: dict[str, Any] | None = None,
) -> int:
    init_memory_system()
    normalized_role = (
        "agent" if role == "agent" else "user" if role in {"user", "operator"} else role
    )
    return add_message(
        session_id=ensure_session(session_id, user_id, scenario),
        user_id=user_id,
        role=normalized_role,
        content=content,
        scenario=scenario,
        summary=summary,
        intent=intent,
        metadata=metadata,
    )


def read_all_history(
    user_id: str = DEFAULT_USER_ID,
    session_id: str = DEFAULT_SESSION_ID,
    *,
    include_cleared: bool = True,
) -> list[dict[str, Any]]:
    init_memory_system()
    return get_session_history(
        session_id,
        user_id,
        include_cleared=include_cleared,
        limit=5000,
    )


def read_history_from_offset(user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
    init_memory_system()
    return get_pending_messages(user_id)


def get_dream_offset(user_id: str = DEFAULT_USER_ID) -> int:
    """Compatibility metric: number of processed messages in the default session."""
    history = read_all_history(user_id, include_cleared=True)
    return sum(1 for item in history if item.get("dream_processed"))


def update_dream_offset(new_offset: int, user_id: str = DEFAULT_USER_ID) -> None:
    history = read_all_history(user_id, include_cleared=True)
    ids = [int(item["id"]) for item in history[: max(0, int(new_offset))]]
    mark_messages_processed(ids)


def get_clear_offset(
    user_id: str = DEFAULT_USER_ID,
    session_id: str = DEFAULT_SESSION_ID,
) -> int:
    all_items = read_all_history(user_id, session_id, include_cleared=True)
    visible_items = read_all_history(user_id, session_id, include_cleared=False)
    return max(0, len(all_items) - len(visible_items))


def update_clear_offset(
    new_offset: int,
    user_id: str = DEFAULT_USER_ID,
    session_id: str = DEFAULT_SESSION_ID,
) -> None:
    # Kept only for callers that still express clearing as an offset. The v2
    # chat router uses memory_store.clear_session directly.
    from services.memory_store import clear_session

    if new_offset > 0:
        clear_session(session_id, user_id)


def pending_history_count(user_id: str = DEFAULT_USER_ID) -> int:
    init_memory_system()
    return count_pending_messages(user_id)


def reset_memory(user_id: str = DEFAULT_USER_ID) -> None:
    reset_user_memory(user_id)
    _ensure_dirs()
    profile_path = _profile_path(user_id)
    if TEMPLATE_PROFILE.exists():
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(
            TEMPLATE_PROFILE.read_text(encoding="utf-8"), encoding="utf-8"
        )
    elif profile_path.exists():
        profile_path.unlink()
