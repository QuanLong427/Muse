"""Protocol validation for the Musicer ReAct agent.

The model is free to choose a plan, but a final answer must not impersonate a
tool call or claim an operation that was never observed.  This module contains
only deterministic checks; business routing remains inside the model and
Skills.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage


_FENCED_BLOCK = re.compile(r"```(?:bash|sh|shell|powershell|cmd)?\s*\n([\s\S]*?)```", re.I)
_LEGACY_TRACK_BLOCK = re.compile(r"```(?:tracks|added)\s*\n[\s\S]*?```", re.I)
_LEGACY_TRACK_JSON = re.compile(
    r"```json\s*\n\s*(?:\[\s*\{|\{\s*\"tracks\"\s*:)", re.I
)
_DEFERRED_ENDINGS = (
    "让我先搜索",
    "让我搜索一下",
    "我先搜索一下",
    "我现在搜索",
    "让我先执行",
    "我现在执行",
    "接下来我会调用",
)
_ACTION_CLAIM_PATTERNS = (
    (
        "播放",
        {"control_player", "play_track", "play_music_playlist", "create_smart_playlist"},
        re.compile(
            r"(?:已经|已)(?:成功)?(?:为(?:您|你))?(?:成功)?(?:开始)?播放|播放成功"
        ),
    ),
    (
        "创建歌单",
        {"create_music_playlist", "manage_music_playlist", "create_smart_playlist", "manage_playlist_draft"},
        re.compile(r"(?:已经|已)(?:成功)?创建(?:了)?歌单|歌单创建成功"),
    ),
    (
        "加入歌单",
        {"add_track_to_music_playlist", "manage_music_playlist", "create_smart_playlist", "manage_playlist_draft"},
        re.compile(r"(?:已经|已)(?:成功)?(?:将[^。！？；;\n]{0,40})?加入(?:了)?歌单"),
    ),
    (
        "修改歌单",
        {"manage_music_playlist"},
        re.compile(r"(?:已经|已)(?:成功)?(?:重命名|删除|复制|重排)(?:了)?歌单"),
    ),
    (
        "修改待播内容",
        {"manage_playback_session"},
        re.compile(r"(?:已经|已)(?:成功)?(?:清空|重排|移除|插入)(?:了)?(?:当前)?(?:播放会话|待播内容|下一首)"),
    ),
    (
        "切换播放模式",
        {"set_playback_mode"},
        re.compile(r"(?:已经|已)(?:成功)?(?:切换|设置)(?:为)?(?:顺序|随机|列表循环|单曲循环|播完停止)"),
    ),
    (
        "记录歌曲反馈",
        {"record_track_feedback"},
        re.compile(
            r"(?:已经|已)(?:成功)?(?:记录|标记)(?:了)?(?:您|你)?(?:对)?[^。！？；;\n]{0,40}"
            r"(?:喜欢|不喜欢|暂时不听|版本反馈)"
        ),
    ),
    (
        "补充推荐歌曲",
        {"recommend_next"},
        re.compile(
            r"(?:已经|已)(?:成功)?(?:将|把)[^。！？；;\n]{0,60}"
            r"(?:推荐歌曲|歌曲)(?:加入|添加)(?:到|进)?(?:了)?(?:播放详情|待播内容|接下来播放)"
        ),
    ),
    (
        "暂停",
        {"control_player"},
        re.compile(r"(?:已经|已)(?:成功)?(?:为(?:您|你))?(?:成功)?暂停|暂停成功"),
    ),
    (
        "下一首",
        {"control_player"},
        re.compile(r"(?:已经|已)(?:成功)?切换到下一首|下一首(?:已经)?开始播放"),
    ),
    (
        "上一首",
        {"control_player"},
        re.compile(r"(?:已经|已)(?:成功)?切换到上一首|上一首(?:已经)?开始播放"),
    ),
    (
        "下载",
        {"convert_video", "create_smart_playlist", "manage_playlist_draft"},
        re.compile(
            r"(?:已经|已)(?:成功)?(?:将[^。！？；;\n]{0,40})?下载"
            r"(?!的|歌曲|音乐|内容|文件|音频|曲目|资源)|下载成功"
        ),
    ),
    (
        "转换",
        {"convert_video", "create_smart_playlist", "manage_playlist_draft"},
        re.compile(r"(?:已经|已)(?:成功)?转换|转换(?:已|已经)?(?:完成|成功)"),
    ),
)

_CAPABILITY_PREFIX = re.compile(
    r"(?:可以|能够|支持|用于|可用于|能帮(?:你|您)?|例如|包括)"
    r"[^。！？；;\n]{0,40}$"
)


@dataclass(frozen=True)
class ProtocolViolation:
    code: str
    message: str


def _message_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return str(content or "")


def observed_tool_names(messages: Iterable[BaseMessage]) -> set[str]:
    """Return tool names backed by actual ToolMessage observations."""
    names: set[str] = set()
    for message in messages:
        if isinstance(message, ToolMessage):
            name = getattr(message, "name", None)
            if isinstance(name, str) and name:
                names.add(name)
    return names


def _latest_tool_payload(
    messages: Iterable[BaseMessage],
    tool_name: str,
) -> dict[str, Any] | None:
    for message in reversed(list(messages)):
        if not isinstance(message, ToolMessage):
            continue
        if getattr(message, "name", None) != tool_name:
            continue
        try:
            payload = json.loads(_message_text(message))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def _latest_smart_result(messages):
    for message in reversed(list(messages)):
        if isinstance(message, ToolMessage) and message.name in {"create_smart_playlist", "manage_playlist_draft"}:
            payload = _latest_tool_payload([message], message.name)
            if message.name == "manage_playlist_draft" and payload and payload.get("action") in {"read", "list"}:
                continue
            return payload
    return None


def _dispatched_client_actions(messages: Iterable[BaseMessage]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        content = getattr(message, "content", "")
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(payload, dict)
            and payload.get("status") == "dispatched"
            and isinstance(payload.get("client_action"), dict)
        ):
            actions.append(payload["client_action"])
    return actions


def dispatched_client_action_ids(messages: Iterable[BaseMessage]) -> list[str]:
    """Return correlated browser action ids in observation order."""
    return [
        str(action["action_id"])
        for action in _dispatched_client_actions(messages)
        if isinstance(action.get("action_id"), str) and action["action_id"]
    ]


def _latest_recommendation_fallback(
    messages: Iterable[BaseMessage],
) -> dict[str, Any] | None:
    for message in reversed(list(messages)):
        if not isinstance(message, ToolMessage) or getattr(message, "name", "") != "recommend_music":
            continue
        try:
            payload = json.loads(_message_text(message))
        except json.JSONDecodeError:
            return None
        notice = str(payload.get("user_notice") or "").strip() if isinstance(payload, dict) else ""
        if notice:
            return payload
        return None
    return None


def _discloses_recommendation_fallback(text: str) -> bool:
    return (
        "本地" in text
        and bool(re.search(r"B站|云端|在线", text, re.I))
        and bool(re.search(r"不足|不可用|失败|调整|补足|只找到|未找到", text))
    )


def _is_capability_description(text: str, claim_start: int) -> bool:
    """Distinguish capability examples from claims about completed actions."""
    prefix = text[max(0, claim_start - 60) : claim_start]
    return bool(_CAPABILITY_PREFIX.search(prefix))


def _action_claims(text: str) -> list[tuple[str, set[str]]]:
    claims: list[tuple[str, set[str]]] = []
    for label, allowed_tools, pattern in _ACTION_CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            if _is_capability_description(text, match.start()):
                continue
            claims.append((label, allowed_tools))
            break
    return claims


def _latest_user_text(messages: Iterable[BaseMessage]) -> str:
    for message in reversed(list(messages)):
        if isinstance(message, HumanMessage):
            return _message_text(message)
    return ""


def validate_final_response(
    text: str,
    messages: Iterable[BaseMessage],
    registered_tools: Iterable[str],
    client_action_results: dict[str, dict[str, Any]] | None = None,
) -> ProtocolViolation | None:
    """Validate a proposed terminal answer without imposing a tool route."""
    stripped = text.strip()
    if not stripped:
        return ProtocolViolation("empty_final", "最终回答为空")

    if _LEGACY_TRACK_BLOCK.search(stripped) or _LEGACY_TRACK_JSON.search(stripped):
        return ProtocolViolation(
            "fabricated_track_cards",
            "回答自行输出了旧版 Track 数据块；交互卡片必须来自 present_tracks 的真实工具结果",
        )

    tool_names = {name for name in registered_tools if name}
    for block in _FENCED_BLOCK.findall(stripped):
        for name in tool_names:
            if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", block):
                return ProtocolViolation(
                    "textual_tool_call",
                    f"回答把注册工具 {name} 写成了代码文本，而没有发出真实工具调用",
                )

    for name in tool_names:
        textual_call = re.compile(
            rf"(?m)^\s*{re.escape(name)}\s*(?:\(|--|\{{)", re.I
        )
        if textual_call.search(stripped):
            return ProtocolViolation(
                "textual_tool_call",
                f"回答把注册工具 {name} 写成了调用文本，而没有发出真实工具调用",
            )

    tail = stripped.rstrip("。.!！?？:： ")
    if any(tail.endswith(ending) for ending in _DEFERRED_ENDINGS):
        return ProtocolViolation(
            "unfinished_action",
            "回答以准备搜索或执行的承诺结束，但没有完成当前请求",
        )

    recommendation_fallback = _latest_recommendation_fallback(messages)
    if recommendation_fallback and not _discloses_recommendation_fallback(stripped):
        return ProtocolViolation(
            "undisclosed_recommendation_fallback",
            "推荐来源发生了动态调整，最终回答必须说明："
            + str(recommendation_fallback.get("user_notice") or ""),
        )

    observed = observed_tool_names(messages)
    draft_mutation_claim = bool(re.search(
        r"(?:已经|已)(?:成功)?(?:(?:将|把|从)[^。！？；;\n]{0,80})?(?:移除|删除|替换|换掉|改名|重排|更新|调整)|草稿(?:已经|已)(?:成功)?(?:更新|修改|调整)", stripped))
    if draft_mutation_claim and re.search(r"草稿|第[一二三四五六七八九十\d]+首", stripped):
        mutation = _latest_smart_result(messages)
        draft = mutation.get("draft") if mutation else None
        saved = isinstance(draft, dict) and draft.get("status") == "saved" and mutation.get("action") == "confirm" and isinstance(mutation.get("playlist"), dict)
        if not saved and (not isinstance(draft, dict) or mutation.get("action") not in {"remove", "replace", "rename", "reorder", "filter_versions", "add", "cancel"} or draft.get("revision", -1) <= mutation.get("previous_revision", -1)):
            return ProtocolViolation("draft_edit_unconfirmed", "没有成功修改草稿的版本回执，不能声称已完成删歌、换歌或调整")
    action_claims = _action_claims(stripped)
    named_playlist_claimed = any(label == "创建歌单" for label, _ in action_claims)
    if (
        named_playlist_claimed
        and observed.intersection({"create_smart_playlist", "manage_playlist_draft"})
        and not observed.intersection({"create_music_playlist", "manage_music_playlist"})
    ):
        smart_result = _latest_smart_result(messages)
        if not smart_result or not isinstance(smart_result.get("playlist"), dict):
            return ProtocolViolation(
                "smart_playlist_not_saved",
                "智能歌单工具只生成了预览或播放会话，没有创建命名歌单",
            )
    download_claimed = any(label in {"下载", "转换"} for label, _ in action_claims)
    smart_result = _latest_smart_result(messages)
    if smart_result:
        draft = smart_result.get("draft")
        if isinstance(draft, dict) and draft.get("status") != "saved" and (
            named_playlist_claimed or any(label == "加入歌单" for label, _ in action_claims)
            or re.search(r"歌单(?:已经|已)(?:成功)?保存|(?:已经|已)(?:成功)?保存(?:了)?(?:智能)?歌单", stripped)
        ):
            return ProtocolViolation("smart_playlist_not_saved", "只生成或修改了歌单草稿，尚未保存或加入命名歌单")
        if named_playlist_claimed and smart_result.get("target_playlist_id"):
            return ProtocolViolation("smart_playlist_append_not_create", "本次操作向已有歌单追加歌曲，并未新建歌单")
        smart_job = smart_result.get("job")
        playlist = smart_result.get("playlist")
        if isinstance(smart_job, dict) and smart_job.get("status") != "completed" and isinstance(playlist, dict):
            confirmed_count = int(playlist.get("added_count") or 0)
            claimed_counts = re.findall(
                r"(?:已经|已)(?:成功)?(?:为[^。！？；;\n]{0,60}?)?(?:添加|加入)(?:了)?\s*(\d+)\s*首", stripped
            )
            if any(int(count) > confirmed_count for count in claimed_counts):
                return ProtocolViolation("playlist_addition_pending", "联网歌曲尚未加入歌单；请分别报告已加入的本地数量与待下载数量，不得声称所有请求歌曲已添加")
        if download_claimed and isinstance(smart_job, dict) and smart_job.get("status") != "completed":
            return ProtocolViolation("download_still_queued", "歌单中的联网歌曲尚未全部下载成功，请报告后台任务的真实状态")
        if any(label == "播放" for label, _ in action_claims) and smart_result.get("status") == "needs_download":
            return ProtocolViolation("smart_playlist_needs_download", "联网歌单仍需下载，未下发完整播放指令")
    if download_claimed and "convert_video" in observed:
        download_result = _latest_tool_payload(messages, "convert_video")
        if download_result and download_result.get("status") == "queued":
            return ProtocolViolation(
                "download_still_queued",
                "下载工具只创建了后台任务，尚未完成下载；请说明任务已进入队列",
            )
        if download_result and download_result.get("success") is False:
            return ProtocolViolation(
                "download_failed",
                "下载工具返回失败，不能声称下载或转换已经成功",
            )
    playback_claimed = any(
        label in {
            "播放",
            "暂停",
            "下一首",
            "上一首",
            "修改待播内容",
            "切换播放模式",
            "补充推荐歌曲",
        }
        for label, _ in action_claims
    )
    dispatched_actions = _dispatched_client_actions(messages)
    if dispatched_actions:
        action_results = client_action_results or {}
        failed: list[dict[str, Any]] = []
        unconfirmed: list[dict[str, Any]] = []
        for action in dispatched_actions:
            action_id = action.get("action_id")
            result = action_results.get(str(action_id)) if action_id else None
            if not isinstance(result, dict) or result.get("status") != "succeeded":
                if isinstance(result, dict) and result.get("status") == "failed":
                    failed.append(result)
                else:
                    unconfirmed.append(action)
        if failed and not re.search(r"(?:失败|未能|无法|错误|没有成功)", stripped):
            error = str(failed[-1].get("result", {}).get("error") or "播放器执行失败")
            return ProtocolViolation(
                "client_action_failed",
                f"浏览器已返回动作失败 ACK：{error}",
            )
        if unconfirmed and playback_claimed:
            return ProtocolViolation(
                "unconfirmed_client_action",
                "播放器工具只确认指令已下发，尚未收到浏览器执行 ACK；请表述为已发送相应指令",
            )
    for claim, allowed_tools in action_claims:
        if not observed.intersection(allowed_tools):
            user_text = _latest_user_text(messages)
            context = f"（当前用户请求：{user_text[:80]}）" if user_text else ""
            return ProtocolViolation(
                "unobserved_action_claim",
                f"回答声称已完成{claim}，但本轮没有对应的真实工具结果{context}",
            )
    return None


def repair_instruction(violation: ProtocolViolation) -> str:
    return (
        "上一份草稿违反了 Musicer 执行协议，不能作为最终回答。"
        f"原因：{violation.message}。"
        "如果任务需要查询或动作，请现在发出真实的结构化工具调用；"
        "如果不需要工具，请直接给出完整最终回答。"
        "不要输出工具命令示例，也不要以‘让我先处理’之类的承诺结束。"
    )


def safe_protocol_response(
    violation: ProtocolViolation,
    messages: Iterable[BaseMessage],
    client_action_results: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Build an evidence-backed terminal response when model repair still fails."""
    message_list = list(messages)
    if violation.code == "draft_edit_unconfirmed":
        payload = _latest_tool_payload(message_list, "manage_playlist_draft") or {}
        error = str(payload.get("error") or "工具参数或执行结果未通过校验")
        return f"本次草稿修改未得到成功回执：{error}。请以卡片中的当前草稿状态为准，检查后再重试。"
    recommendation_fallback = _latest_recommendation_fallback(message_list)
    recommendation_notice = (
        str(recommendation_fallback.get("user_notice") or "").strip()
        if recommendation_fallback
        else ""
    )
    actions = _dispatched_client_actions(message_list)
    results = client_action_results or {}
    if actions:
        action = actions[-1]
        action_id = str(action.get("action_id") or "")
        result = results.get(action_id, {})
        status = result.get("status")
        action_name = str(action.get("action") or "播放器操作")
        track = action.get("track")
        track_title = (
            str(track.get("title") or "") if isinstance(track, dict) else ""
        )
        subject = f"《{track_title.strip('《》')}》" if track_title else "该播放器操作"
        if status == "succeeded":
            if action_name == "play_collection":
                tracks = action.get("tracks")
                count = len(tracks) if isinstance(tracks, list) else 0
                return f"播放器已确认执行成功：已加载并开始播放 {count} 首歌曲。"
            if action_name in {"play", "play_track", "next", "previous"}:
                return f"播放器已确认执行成功：正在播放{subject}。"
            return "播放器已确认执行成功。"
        if status == "failed":
            error = str(result.get("result", {}).get("error") or "未知错误")
            return f"播放器执行失败：{error}"
        return "播放指令已经发送，但暂未收到浏览器的执行确认。请检查播放器状态。"

    download_result = _latest_tool_payload(message_list, "convert_video")
    if download_result:
        if download_result.get("status") == "queued":
            job_id = str(download_result.get("job_id") or "").strip()
            suffix = f"（任务 {job_id[:8]}）" if job_id else ""
            return f"下载任务已进入后台队列{suffix}，可在“下载任务”中查看进度、取消或重试。"
        if download_result.get("success") is False:
            errors = download_result.get("errors")
            if isinstance(errors, list) and errors and isinstance(errors[-1], dict):
                detail = str(errors[-1].get("message") or "下载任务创建失败")
                return f"下载任务未能启动：{detail}"
            return "下载任务未能启动，请查看工具错误后重试。"

    smart_result = _latest_smart_result(message_list)
    if smart_result:
        draft = smart_result.get("draft")
        if isinstance(draft, dict) and draft.get("status") == "draft":
            return f"已更新歌单草稿《{draft.get('name') or '智能歌单'}》，共 {len(draft.get('items') or [])} 首。尚未保存或下载；请在卡片中调整后确认添加。"
        playlist = smart_result.get("playlist")
        if isinstance(playlist, dict):
            if smart_result.get("target_playlist_id"):
                count = int(playlist.get("added_count") or 0)
                suffix = "联网歌曲已进入后台下载任务，成功后加入同一歌单。" if smart_result.get("job") else "没有待下载歌曲。"
                return f"歌单“{str(playlist.get('name') or '未命名')}”已更新，本批已加入 {count} 首本地歌曲。{suffix}"
            if smart_result.get("job"):
                return f"已创建智能歌单“{str(playlist.get('name') or '未命名歌单')}”，本地歌曲已加入，联网歌曲正在后台下载，成功后自动加入；可在下载任务中查看结果或重试。"
            return f"智能歌单已保存为“{str(playlist.get('name') or '未命名歌单')}”。"
        count = int(smart_result.get("result_count") or len(smart_result.get("tracks") or []))
        if smart_result.get("status") == "needs_confirmation":
            return (
                f"已生成包含 {count} 首歌曲的智能歌单预览。"
                "由于存在未满足或无法核验的约束，本次没有修改播放内容或命名歌单。"
            )
        if count:
            remote_count = len(smart_result.get("remote_candidates") or [])
            return f"已生成包含 {count} 首歌曲的智能歌单预览，其中 {remote_count} 首需要下载；添加为歌单时会下载联网歌曲。"

    for message in reversed(message_list):
        if not isinstance(message, ToolMessage) or getattr(message, "name", "") != "present_tracks":
            continue
        try:
            payload = json.loads(_message_text(message))
        except json.JSONDecodeError:
            continue
        tracks = payload.get("tracks") if isinstance(payload, dict) else None
        if isinstance(tracks, list) and tracks:
            presentation = (
                f"已找到 {len(tracks)} 个可验证的歌曲结果，并展示为歌曲卡片。"
                "你可以直接从卡片中选择播放、加入待播或下载。"
            )
            return f"{recommendation_notice}\n\n{presentation}" if recommendation_notice else presentation

    if violation.code == "fabricated_track_cards":
        return "歌曲数据未通过真实性校验，因此没有展示未经工具确认的候选。"
    if violation.code in {"textual_tool_call", "unfinished_action"}:
        return "这次没有形成真实工具执行结果，因此未执行相关操作。请直接告诉我具体目标后重试。"
    return f"本次请求未形成可验证结果：{violation.message}"
