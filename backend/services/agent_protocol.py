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

from langchain_core.messages import BaseMessage, ToolMessage


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
_ACTION_CLAIMS = {
    "播放": {"control_player", "play_track"},
    "暂停": {"control_player"},
    "下一首": {"control_player"},
    "上一首": {"control_player"},
    "下载": {"convert_video"},
    "转换完成": {"convert_video"},
}


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
    definite_client_claims = (
        "已播放",
        "已经播放",
        "播放成功",
        "已暂停",
        "已切换到下一首",
        "已切换到上一首",
    )
    if _has_dispatched_client_action(messages) and any(
        claim in stripped for claim in definite_client_claims
    ):
        return ProtocolViolation(
            "unconfirmed_client_action",
            "播放器工具只确认指令已下发，尚未收到浏览器执行 ACK；请表述为已发送相应指令",
        )
    success_words = (
        "已播放",
        "已经播放",
        "播放成功",
        "已暂停",
        "已下载",
        "已经下载",
        "下载成功",
        "转换完成",
    )
    if any(word in stripped for word in success_words):
        for claim, allowed_tools in _ACTION_CLAIMS.items():
            if claim in stripped and not observed.intersection(allowed_tools):
                return ProtocolViolation(
                    "unobserved_action_claim",
                    f"回答声称已{claim}，但本轮没有对应的真实工具结果",
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
