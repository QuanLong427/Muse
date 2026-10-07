"""P0/P1 business continuity and evidence boundaries; all storage is isolated."""
import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool

from services import memory_store as store, ai_agent, agent_context
from services.episode_memory import archive_turn_episode
from services.episode_evidence import entity_refs, turn_evidence
from services.memory_task_service import list_tasks, record_task_episode, task_id
from services.memory_manager import get_relevant_episode_context, get_structured_memory_context


def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "MEMORY_DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "MEMORY_DB_PATH", tmp_path / "memory.db")
    store.init_memory_db()


def result(name, **payload):
    return {"phase": "result", "name": name, "content": json.dumps(payload, ensure_ascii=False)}


def archive(**kwargs):
    defaults = dict(user_id="u", session_id="s", scenario="跑步", user_message="创建歌单",
                    source_message_ids=[], tool_events=[], final_text="完成")
    return archive_turn_episode(**(defaults | kwargs))


def test_current_player_fields_and_json_entities_are_preserved(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    episode = archive(player_state={"current": {"id": "track1", "title": "歌", "url": "private"},
        "playing": False, "progress": 10, "playback_session_id": "ps", "session_items": [{}]},
        tool_events=[result("create_music_playlist", status="created", error=None,
                            playlist={"id": "p", "items": [{"track": {"id": "t"}}]})])
    assert episode["context"]["player"] == {"current": {"id": "track1", "title": "歌"},
                                           "playing": False, "progress": 10, "playback_session_id": "ps"}
    assert episode["entity_refs"]["playlist_id"] == ["p"]
    assert episode["entity_refs"]["track_id"] == ["t"]
    assert episode["result_status"] == "success"


def test_prose_and_candidates_do_not_become_selected_or_receipts():
    evidence = turn_evidence([result("local_search", tracks=[{"id": "candidate"}], total=1),
                             result("manage_playlist_draft", status="draft", draft={"id": "d", "items": [{"track": {"id": "suggestion"}}]})],
                             selected_tracks=[{"id": "chosen"}])
    assert evidence["entity_roles"]["selected"]["track_id"] == ["chosen"]
    assert evidence["entity_roles"]["candidates"]["track_id"] == ["candidate"]
    assert evidence["entity_roles"]["draft_selection"]["track_id"] == ["suggestion"]
    assert evidence["entity_roles"]["downloaded"] == {}
    assert entity_refs({"content": '{"job":{"id":"j"},"draft":{"id":"d"},"batch_id":"b"}'}) == {
        "batch_id": ["b"], "draft_id": ["d"], "job_id": ["j"]}


def test_dispatched_is_not_success_and_ack_overrides_only_matching_action(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    event = result("control_player", status="dispatched", action_id="a")
    pending = archive(tool_events=[event], client_action_results={"other": {"status": "succeeded"}})
    assert pending["result_status"] == "partial"
    succeeded = archive(tool_events=[event], client_action_results={"a": {"id": "a", "status": "succeeded"}}, error="provider stopped")
    assert succeeded["result_status"] == "partial"
    assert succeeded["context"]["receipts"][0]["phase"] == "ack"
    failed = archive(tool_events=[event], client_action_results={"a": {"status": "failed"}})
    assert failed["result_status"] == "failed"


def test_skill_read_and_search_are_observations_but_script_failure_is_not_success(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    success = archive(user_message="推荐歌曲", tool_events=[{"phase": "result", "name": "activate_skill", "content": "# Instructions"},
        result("local_search", total=0, tracks=[])])
    assert success["result_status"] == "success"
    assert success["episode_type"] == "recommendation"
    failed = archive(tool_events=[result("execute_skill_script", status="completed", stdout='{"status":"failed","error":"bad"}')])
    assert failed["result_status"] == "failed"
    assert failed["episode_type"] != "knowledge"


def test_unreturned_call_and_fake_final_ids_do_not_prove_execution(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    episode = archive(tool_events=[{"phase": "call", "name": "convert_video", "input": {}}],
                      final_text='下载成功 {"job_id":"invented"}')
    assert episode["result_status"] == "failed"
    assert "job_id" not in episode["entity_refs"]
    assert list_tasks("u", "s") == []
    partial = archive(tool_events=[result("local_search", tracks=[]),
        {"phase": "call", "name": "convert_video", "input": {}}])
    assert partial["result_status"] == "partial"
    assert partial["context"]["unreturned_tool_calls"] == ["convert_video"]


def test_task_links_survive_multiple_turns_and_are_idempotent_and_scoped(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    first_events = [result("create_smart_playlist", status="draft", draft={"id": "d", "batch_id": "b", "items": []})]
    first = archive(tool_events=first_events)
    record_task_episode(first, first_events)
    second = archive(user_message="确认保存", tool_events=[result("manage_playlist_draft", status="queued",
        draft={"id": "d", "items": []}, playlist={"id": "p"}, job={"id": "j", "status": "queued"})])
    task = list_tasks("u", "s")[0]
    assert task["goal"] == "创建歌单"
    assert set(task["episode_ids"]) == {first["id"], second["id"]}
    assert task["entity_refs"]["job_id"] == ["j"]
    assert task["entity_refs"]["playlist_id"] == ["p"]
    assert len(list_tasks("u", "s")) == 1
    stored = store.list_memory_episodes("u")[0]
    assert stored["context"]["task_ids"] == [task["id"]]
    assert list_tasks("other", "s") == []
    assert list_tasks("u", "other") == []
    assert task_id("u:a", "s", "download", "j") != task_id("u", "a:s", "download", "j")


def test_task_api_is_read_only_and_scope_is_explicit(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import memory
    seen = []
    monkeypatch.setattr(agent_context, "live_task_context", lambda user, session: seen.append((user, session)) or {"tasks": []})
    app = FastAPI()
    app.include_router(memory.router)
    with TestClient(app) as client:
        response = client.get("/api/memory/tasks?user_id=u&session_id=s")
        assert response.status_code == 200 and response.json() == {"tasks": []}
        assert client.get("/api/memory/tasks?session_id=").status_code == 422
    assert seen == [("u", "s")]


def test_scene_filter_and_failure_recall_are_separate(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    archive(scenario="睡觉", user_message="睡觉歌单不要摇滚")
    assert store.search_memory_episodes("歌单摇滚", "u", scenario="跑步") == []
    failure = archive(user_message="下载歌曲失败 412", tool_events=[result("convert_video", status="failed", error="412")])
    assert store.search_memory_episodes("下载歌曲失败", "u", scenario="跑步") == []
    context = get_relevant_episode_context("u", "跑步", "下载歌曲失败 412，请重试")
    assert failure["id"] in context["episode_ids"]
    assert "失败经历仅作风险" in context["text"]


def test_mandatory_constraints_not_lost_to_six_item_limit(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    for index in range(8):
        store.upsert_memory_item(user_id="u", kind="avoidance", scenario="跑步",
                                 memory_key=f"rule{index}", directive=f"禁止项目{index}", confidence=0.9)
    context = get_structured_memory_context("u", "跑步", "生成歌单", limit=6)
    assert all(f"禁止项目{index}" in context for index in range(8))


def test_context_reads_latest_revision_and_does_not_grant_confirmation(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    from services import playlist_draft_service
    revision = [0]
    def drafts(user_id, session_id):
        assert (user_id, session_id) == ("u", "s")
        return [dict(id="d", revision=revision[0], status="draft", name="夜跑", scenario="跑步", items=[], receipt=None)]
    monkeypatch.setattr(playlist_draft_service, "list_drafts", drafts)
    builder = agent_context.ContextBuilder(user_id="u", session_id="s", scenario="跑步", query="继续修改歌单")
    assert '"revision": 0' in builder.runtime_text()
    revision[0] = 2
    assert '"revision": 2' in builder.runtime_text()
    assert "不构成新的执行授权" in builder.runtime_text()


def test_simple_transport_context_skips_tasks(monkeypatch):
    monkeypatch.setattr(agent_context, "memory_text", lambda *args: "")
    monkeypatch.setattr(agent_context, "live_task_context", lambda *args: (_ for _ in ()).throw(AssertionError("unneeded")))
    text = agent_context.ContextBuilder(user_id="u", session_id="s", scenario="默认", query="下一首").runtime_text()
    assert "当前会话任务" not in text


def test_live_download_completion_is_not_frozen_in_memory(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    from services import playlist_draft_service, download_job_service
    archive(tool_events=[result("convert_video", status="queued", job={"id": "j", "status": "queued"})])
    monkeypatch.setattr(playlist_draft_service, "list_drafts", lambda *args: [])
    status = ["queued"]
    monkeypatch.setattr(download_job_service, "get_download_job", lambda identifier, **kwargs: {
        "id": identifier, "status": status[0], "completed_items": 1 if status[0] == "completed" else 0})
    assert agent_context.live_task_context("u", "s")["tasks"][0]["download_job"]["status"] == "queued"
    status[0] = "completed"
    assert agent_context.live_task_context("u", "s")["tasks"][0]["download_job"]["completed_items"] == 1


def test_multiple_drafts_expose_ambiguity_without_selecting_one(monkeypatch, tmp_path):
    isolated(monkeypatch, tmp_path)
    from services import playlist_draft_service
    monkeypatch.setattr(playlist_draft_service, "list_drafts", lambda *args: [
        dict(id=identifier, revision=0, status="draft", name="同名", items=[]) for identifier in ("d1", "d2")])
    context = agent_context.live_task_context("u", "s")
    assert context["ambiguous_draft_reference"]
    assert context["pending_draft_ids"] == ["d1", "d2"]
    assert "active_draft_id" not in context


def test_projection_is_not_duplicated_and_absent_scene_does_not_fallback():
    text = agent_context.memory_text("u", "睡觉", "推荐", profile_reader=lambda *args:
        "## 全局基准\n- 音量小一点\n## 场景:默认\n- 摇滚\n",
        scenario_reader=lambda *args: "", memory_reader=lambda *args: "")
    assert "音量小一点" in text
    assert "摇滚" not in text
    text = agent_context.memory_text("u", "跑步", "推荐", profile_reader=lambda *args: "",
        scenario_reader=lambda *args: "# 跑步\n## 结构化长期记忆\n- 不要慢歌",
        memory_reader=lambda *args: "- 不要慢歌")
    assert text.count("不要慢歌") == 1


def test_context_is_refreshed_inside_react_loop(monkeypatch):
    rounds = []
    @tool
    def inspect_again() -> str:
        """Read a fresh observation."""
        return '{"status":"ok"}'
    def model(messages):
        rounds.append(messages)
        if len(rounds) == 1:
            return AIMessage(content="", tool_calls=[{"name": "inspect_again", "args": {}, "id": "call"}])
        return AIMessage(content="已读取")
    class Model:
        def bind_tools(self, tools):
            return RunnableLambda(model)
    class Builder:
        def runtime_text(self):
            return f"live_revision={len(rounds)}"
    monkeypatch.setattr(ai_agent, "create_chat_model", lambda **kwargs: Model())
    monkeypatch.setattr(ai_agent, "_build_tools", lambda *args, **kwargs: [inspect_again])
    graph = ai_agent._build_agent("base", context_builder=Builder())
    asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="查询状态")]}))
    assert rounds[0][1].content == "live_revision=0"
    assert rounds[1][1].content == "live_revision=1"


def test_sse_archiving_receives_the_real_graph_ack(monkeypatch):
    archives = []
    ack = {"a": {"id": "a", "status": "succeeded", "result": {"action": "pause"}}}
    class Graph:
        async def astream_events(self, *args, **kwargs):
            yield {"event": "on_tool_start", "name": "control_player", "data": {"input": {"action": "pause"}}}
            yield {"event": "on_tool_end", "name": "control_player", "data": {"output": '{"status":"dispatched","action_id":"a"}'}}
            yield {"event": "on_chain_end", "data": {"output": {"client_action_results": ack}}}
            yield {"event": "on_chain_end", "data": {"output": {"messages": [AIMessage(content="已暂停")], "protocol_status": "valid"}}}
    monkeypatch.setattr(ai_agent, "ensure_session", lambda *args: "s")
    monkeypatch.setattr(ai_agent, "get_recent_messages", lambda *args, **kwargs: [])
    monkeypatch.setattr(ai_agent, "append_history", lambda **kwargs: 1)
    monkeypatch.setattr(ai_agent, "get_relevant_episode_context", lambda **kwargs: {"episode_ids": [], "text": ""})
    monkeypatch.setattr(ai_agent, "_build_system_prompt", lambda *args, **kwargs: "base")
    monkeypatch.setattr(ai_agent, "_build_agent", lambda *args, **kwargs: Graph())
    monkeypatch.setattr(ai_agent, "_maybe_trigger_dream", lambda *args: None)
    monkeypatch.setattr(ai_agent, "archive_turn_episode", lambda **kwargs: archives.append(kwargs))
    async def collect():
        return [event async for event in ai_agent.chat_stream("暂停", history=[], user_id="u", session_id="s")]
    events = asyncio.run(collect())
    assert archives[0]["client_action_results"] == ack
    assert events[-1]["data"]["status"] == "completed"
