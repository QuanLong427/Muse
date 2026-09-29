import json
import os
import sys

from langchain_core.messages import ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ai_agent import _build_tools, _extract_client_action


def _tools_by_name(player_state=None):
    return {
        tool.name: tool
        for tool in _build_tools(player_state=player_state)
    }


def test_player_tools_are_available_and_return_snapshot():
    snapshot = {
        "available": True,
        "current": {"id": "track-1", "title": "测试歌曲"},
        "playing": True,
        "volume": 0.8,
    }
    tools = _tools_by_name(snapshot)

    assert "get_player_state" in tools
    assert "control_player" in tools
    assert "search_memory" in tools
    assert "remember_preference" in tools
    assert "forget_preference" in tools
    assert json.loads(tools["get_player_state"].invoke({})) == snapshot


def test_control_player_dispatches_typed_client_action():
    tools = _tools_by_name({"available": True})

    result = json.loads(
        tools["control_player"].invoke({"action": "set_volume", "value": 0.5})
    )

    assert result == {
        "status": "dispatched",
        "client_action": {
            "target": "player",
            "action": "set_volume",
            "value": 0.5,
        },
    }


def test_control_player_rejects_invalid_value_and_missing_client():
    available = _tools_by_name({"available": True})["control_player"]
    unavailable = _tools_by_name()["control_player"]

    assert json.loads(
        available.invoke({"action": "set_volume", "value": 2})
    )["status"] == "invalid"
    assert json.loads(
        available.invoke({"action": "seek", "value": -1})
    )["status"] == "invalid"
    assert json.loads(unavailable.invoke({"action": "next"}))["status"] == "unavailable"


def test_extract_client_action_supports_langgraph_tool_message():
    output = ToolMessage(
        content=json.dumps(
            {
                "status": "dispatched",
                "client_action": {"target": "player", "action": "next"},
            }
        ),
        tool_call_id="tool-call-1",
    )

    assert _extract_client_action(output) == {
        "target": "player",
        "action": "next",
    }
    assert _extract_client_action("not-json") is None
