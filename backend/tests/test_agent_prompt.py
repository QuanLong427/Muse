import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import ai_agent
from services.ai_agent import _build_system_prompt


def test_system_prompt_uses_react_principles_instead_of_fixed_tool_routes():
    prompt = _build_system_prompt("默认")

    assert "## ReAct 决策原则" in prompt
    assert "不预设固定工具顺序" in prompt
    assert "最后一条用户消息视为当前目标" in prompt
    assert "能力说明、使用帮助、概念解释" in prompt
    assert "## 可用 Agent Skills" in prompt
    assert "llm-wiki" in prompt
    assert "不得用代码块、命令文本或函数调用示例代替真实工具调用" in prompt
    assert "不要重新输出 tracks JSON" in prompt

    assert "必须使用提供的工具" not in prompt
    assert "路由优先级" not in prompt
    assert "wiki_search 使用场景" not in prompt
    assert "先调用 activate_skill" not in prompt


def test_system_prompt_includes_global_and_current_scenario_profile(monkeypatch):
    profile = """# Profile

## 全局基准
- 永远不要推荐低质量翻唱

## 场景:默认
- 默认偏好

## 场景:跑步
- 优先高节奏音乐

## 场景:睡觉
- 优先轻音乐
"""
    monkeypatch.setattr(ai_agent, "read_profile", lambda user_id="local": profile)
    monkeypatch.setattr(
        ai_agent,
        "read_scenario_profile",
        lambda scenario="默认", user_id="local": (
            "# 场景偏好：跑步\n- 优先高节奏音乐" if scenario == "跑步" else ""
        ),
    )
    monkeypatch.setattr(
        ai_agent,
        "get_structured_memory_context",
        lambda user_id, scenario, query="": "- [跑步] 不要推荐慢歌",
    )

    prompt = _build_system_prompt("跑步", "user-a")

    assert "永远不要推荐低质量翻唱" in prompt
    assert "优先高节奏音乐" in prompt
    assert "优先轻音乐" not in prompt
    assert "不要推荐慢歌" in prompt


def test_system_prompt_includes_only_preselected_episode_context():
    prompt = _build_system_prompt(
        "默认",
        "user-a",
        episode_context="## 与当前请求相关的过往事件\n- 夜跑歌单不要慢歌",
    )

    assert "与当前请求相关的过往事件" in prompt
    assert "夜跑歌单不要慢歌" in prompt
