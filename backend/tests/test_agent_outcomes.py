import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool

from services import ai_agent
from services.agent_outcomes import ExecutionBudgetExceeded, interrupted_response, observed_progress


def _result(name, payload):
    return {"phase": "result", "name": name, "content": json.dumps(payload, ensure_ascii=False)}


def test_interrupted_response_reports_partial_writes_not_fake_downloads():
    events = [_result("add_track_to_music_playlist", {"status": "added", "playlist": {
        "id": "p", "name": "歌单一", "items": [{}, {}, {}, {}]}}),
        _result("bili_search", {"status": "ok", "videos": [{}]})]
    text = interrupted_response(events, error=ExecutionBudgetExceeded())
    assert "4 首本地歌曲" in text
    assert "未观察到下载任务" in text
    assert "未全部完成" in text
    assert "未回滚" in text


def test_interrupted_response_deduplicates_receipts_and_does_not_leak_errors():
    payload = {"status": "queued", "playlist": {"id": "p", "name": "歌单", "items": []},
               "job": {"id": "job-1", "status": "queued", "total_items": 6}}
    events = [_result("create_smart_playlist", payload)] * 2
    text = interrupted_response(events, error=RuntimeError("api_key=secret"))
    assert text.count("下载任务 job-1") == 1
    assert "0/6" in text
    assert "secret" not in text


def test_unexecuted_calls_failed_results_and_searches_are_not_success_receipts():
    events = [{"phase": "call", "name": "create_music_playlist", "input": {}},
              _result("create_music_playlist", {"status": "invalid", "error": "bad"}),
              _result("web_fetch", {"status": "created", "playlist": {"id": "p"}})]
    assert not any(observed_progress(events).values())


def test_local_draft_confirmation_is_reported_if_agent_interrupts():
    events = [_result("manage_playlist_draft", {"status": "saved", "playlist": {"id": "p", "name": "本地歌单", "items": [{}]}})]
    assert observed_progress(events)["playlists"]["p"]["name"] == "本地歌单"
    assert "保存成功" in interrupted_response(events, error=RuntimeError("interrupted"))


def test_graph_stops_before_recursion_limit_without_more_llm_calls(monkeypatch):
    calls = []

    @tool
    def read_again() -> str:
        """Read another observation."""
        return '{"status":"empty"}'

    def endless_model(messages):
        calls.append(messages)
        return AIMessage(content="", tool_calls=[{"name": "read_again", "args": {}, "id": str(len(calls))}])

    class Model:
        def bind_tools(self, tools):
            return RunnableLambda(endless_model)

    monkeypatch.setattr(ai_agent, "create_chat_model", lambda **kwargs: Model())
    monkeypatch.setattr(ai_agent, "_build_tools", lambda *args, **kwargs: [read_again])
    graph = ai_agent._build_agent("test")
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="help")]}, config={"recursion_limit": 50}))
    assert len(calls) == ai_agent.MAX_AGENT_TOOL_ROUNDS
    assert result["protocol_status"] == "interrupted"
    assert "未全部完成" in result["messages"][-1].content


def test_stream_failure_returns_and_persists_evidence_summary(monkeypatch):
    stored = []
    archives = []

    class FailingGraph:
        async def astream_events(self, *args, **kwargs):
            yield {"event": "on_tool_end", "name": "add_track_to_music_playlist", "data": {
                "output": json.dumps({"status": "added", "playlist": {"id": "p", "name": "歌单一", "items": [{}, {}, {}, {}]}})}}
            raise RuntimeError("private-provider-error")

    monkeypatch.setattr(ai_agent, "ensure_session", lambda *args: "s")
    monkeypatch.setattr(ai_agent, "get_recent_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(ai_agent, "append_history", lambda **kwargs: stored.append(kwargs) or len(stored))
    monkeypatch.setattr(ai_agent, "get_relevant_episode_context", lambda *args, **kwargs: {"episode_ids": [], "text": ""})
    monkeypatch.setattr(ai_agent, "_build_system_prompt", lambda *args, **kwargs: "test")
    monkeypatch.setattr(ai_agent, "_build_agent", lambda *args, **kwargs: FailingGraph())
    monkeypatch.setattr(ai_agent, "archive_turn_episode", lambda **kwargs: archives.append(kwargs))

    async def collect():
        return [event async for event in ai_agent.chat_stream("加入10首歌", history=[], session_id="s")]

    events = asyncio.run(collect())
    results = [event["data"] for event in events if event.get("data", {}).get("type") == "result"]
    assert results[0]["subtype"] == "partial"
    assert "4 首本地歌曲" in results[0]["result"]
    assert stored[-1]["role"] == "agent"
    assert archives[0]["final_text"] == stored[-1]["content"]
    assert events[-1]["data"]["status"] == "interrupted"
    assert not any(event["event"] == "error" for event in events)
