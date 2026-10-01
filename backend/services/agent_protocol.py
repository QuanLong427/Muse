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
from typing import Iterable

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage


_FENCED_BLOCK = re.compile(r"```(?:bash|sh|shell|powershell|cmd)?\s*\n([\s\S]*?)```", re.I)
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
        {"control_player", "play_track"},
        re.compile(
            r"(?:已经|已)(?:成功)?(?:为(?:您|你))?(?:成功)?(?:开始)?播放|播放成功"
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
        {"convert_video"},
        re.compile(
            r"(?:已经|已)(?:成功)?(?:将[^。！？；;\n]{0,40})?下载"
            r"(?!的|歌曲|音乐|内容|文件|音频|曲目|资源)|下载成功"
        ),
    ),
    (
        "转换",
        {"convert_video"},
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


def _has_dispatched_client_action(messages: Iterable[BaseMessage]) -> bool:
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
            return True
    return False


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
) -> ProtocolViolation | None:
    """Validate a proposed terminal answer without imposing a tool route."""
    stripped = text.strip()
    if not stripped:
        return ProtocolViolation("empty_final", "最终回答为空")

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

    observed = observed_tool_names(messages)
    action_claims = _action_claims(stripped)
    playback_claimed = any(
        label in {"播放", "暂停", "下一首", "上一首"}
        for label, _ in action_claims
    )
    if _has_dispatched_client_action(messages) and playback_claimed:
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
