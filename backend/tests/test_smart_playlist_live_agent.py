"""Opt-in real-Qwen routing check with isolated stores and simulated Bili media.

MUSICER_LIVE_AGENT_TEST=1 enables provider calls. No real songs are downloaded.
"""

import asyncio
import json
import os

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from models import Track
from services import ai_agent, smart_playlist_service as service, music_library_store as store, download_job_service as jobs, playlist_draft_service as drafts
from services import memory_store, agent_context
from services.episode_memory import archive_turn_episode
from services.memory_task_service import list_tasks


@pytest.mark.skipif(os.getenv("MUSICER_LIVE_AGENT_TEST") != "1", reason="opt-in real provider test")
def test_real_qwen_previews_edits_and_confirms_existing_playlist(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_store, "MEMORY_DATA_DIR", tmp_path)
    monkeypatch.setattr(memory_store, "MEMORY_DB_PATH", tmp_path / "memory.db")
    monkeypatch.setattr(agent_context, "memory_text", lambda *args: "")
    for module, filename in [(store, "library.db"), (jobs, "downloads.db")]:
        monkeypatch.setattr(module, "_DB_DIR", tmp_path)
        monkeypatch.setattr(module, "_DB_PATH", tmp_path / filename)
    owner = "isolated-live-agent"
    target = store.create_playlist("歌单一", user_id=owner)
    catalog = [Track(id=f"local-{i}", title=f"本地曲{i}", author="周杰伦", date="", filename=f"{i}.mp3", subDir="", size=1, url=f"/api/tracks/{i}") for i in range(7)]
    for track in catalog[:4]:
        target = store.add_playlist_track(target["id"], track=track.model_dump(), expected_revision=None, user_id=owner)
    monkeypatch.setattr(service, "scan_tracks", lambda: catalog)
    monkeypatch.setattr(drafts, "scan_tracks", lambda: catalog)
    monkeypatch.setattr(service, "find_track_by_id", lambda identity: next((t for t in catalog if t.id == identity), None))
    monkeypatch.setattr(service, "find_track_by_bvid", lambda bvid: None)
    monkeypatch.setattr(service, "list_recent_tracks", lambda **kwargs: [])
    monkeypatch.setattr(service, "list_feedback_excluded_track_ids", lambda *args: set())
    monkeypatch.setattr(service, "list_memory_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(service, "_probe_duration", lambda track: 240)
    monkeypatch.setattr(service, "_memory_preference_seeds", lambda *args: {"artists": ["周杰伦"], "songs": [], "genres": []})
    monkeypatch.setattr(service, "_wiki_recommendation_context", lambda *args: {"status": "no_results", "artists": [], "songs": [], "genres": []})
    monkeypatch.setattr(service, "build_recent_preference_profile", lambda *args, **kwargs: {
        "generated_at": "test", "policy_version": "test", "windows": [{"days": 7, "tracks": [], "artists": []}]})
    monkeypatch.setattr(ai_agent, "_search_bilibili_with_retry", lambda keyword: {"status": "ok", "videos": [
        {"bvid": f"BV{i:010d}", "title": f"周杰伦《联网曲{i}》", "author": "UP主", "duration": "4:00", "play": 1000-i}
        for i in range(12)]})
    create_job = jobs.create_download_job
    monkeypatch.setattr(jobs, "create_download_job", lambda **kwargs: create_job(**{**kwargs, "schedule": False}))
    # Use the real metadata/system rules but do not load real user's profile.
    monkeypatch.setattr(ai_agent, "read_profile", lambda *args: "")
    monkeypatch.setattr(ai_agent, "get_structured_memory_context", lambda *args: "")
    graph = ai_agent._build_agent(ai_agent._build_system_prompt(user_id=owner), user_id=owner, session_id="live-draft", current_request="帮我加入10首歌到刚刚创建的歌单", player_state={"available": False})
    result = asyncio.run(graph.ainvoke({"messages": [
        HumanMessage(content="帮我创建一个歌单，歌单取名为歌单一"),
        AIMessage(content=f"成功创建歌单一，ID: {target['id']}，目前包含4首歌曲。"),
        HumanMessage(content="帮我加入10首歌到刚刚创建的歌单"),
    ]}, config={"recursion_limit": 50}))
    observations = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    names = [m.name for m in observations]
    print("LIVE_TRACE", names)
    print("LIVE_FINAL", result["messages"][-1].content)
    assert "activate_skill" in names
    assert "create_smart_playlist" in names
    assert not any(call["name"] == "manage_music_playlist" and call["args"].get("action") == "save_current"
                   for message in result["messages"] if isinstance(message, AIMessage) for call in message.tool_calls)
    assert "recommend_next" not in names
    assert len(store.list_playlists(owner)) == 1
    assert len(store.get_playlist(target["id"], owner)["items"]) == 4
    assert jobs.list_download_jobs(user_id=owner) == []
    draft = drafts.list_drafts(owner, "live-draft")[0]
    assert draft["status"] == "draft" and len(draft["items"]) == 10
    assert draft["target_playlist_id"] == target["id"]

    def record_turn(result, request, offset=0):
        events = []
        for message in result["messages"][offset:]:
            if isinstance(message, AIMessage):
                events.extend({"phase": "call", "name": call["name"], "input": call["args"]} for call in message.tool_calls)
            elif isinstance(message, ToolMessage):
                events.append({"phase": "result", "name": message.name, "content": message.content})
        return archive_turn_episode(user_id=owner, session_id="live-draft", scenario="默认", user_message=request,
            source_message_ids=[], tool_events=events, final_text=result["messages"][-1].content)

    record_turn(result, "帮我加入10首歌到刚刚创建的歌单")

    request = "删除这份草稿的第三首，其余歌曲保留，不要保存。"
    prompt = ai_agent._build_system_prompt(user_id=owner, include_memory=False)
    graph = ai_agent._build_agent(prompt, user_id=owner, session_id="live-draft", current_request=request, player_state={"available": False},
        context_builder=agent_context.ContextBuilder(user_id=owner, session_id="live-draft", scenario="默认", query=request))
    edited = asyncio.run(graph.ainvoke({"messages": [*result["messages"], HumanMessage(content=request)]}, config={"recursion_limit": 50}))
    print("LIVE_EDIT_CALLS", [m.tool_calls for m in edited["messages"][len(result["messages"]):] if isinstance(m, AIMessage) and m.tool_calls])
    print("LIVE_EDIT_TRACE", [(m.name, m.content[:600]) for m in edited["messages"][len(result["messages"]):] if isinstance(m, ToolMessage)])
    print("LIVE_EDIT_FINAL", edited["messages"][-1].content)
    updated = drafts.get_draft(draft["id"], owner)
    assert updated["revision"] == 1 and len(updated["items"]) == 9
    assert [item["item_id"] for item in updated["items"]] == [item["item_id"] for i, item in enumerate(draft["items"]) if i != 2]
    assert jobs.list_download_jobs(user_id=owner) == []
    record_turn(edited, request, len(result["messages"]))

    request = "就这样，确认添加。"
    prompt = ai_agent._build_system_prompt(user_id=owner, include_memory=False)
    graph = ai_agent._build_agent(prompt, user_id=owner, session_id="live-draft", current_request=request, player_state={"available": False},
        context_builder=agent_context.ContextBuilder(user_id=owner, session_id="live-draft", scenario="默认", query=request))
    confirmed = asyncio.run(graph.ainvoke({"messages": [*edited["messages"], HumanMessage(content=request)]}, config={"recursion_limit": 50}))
    print("LIVE_CONFIRM", confirmed["messages"][-1].content)
    assert drafts.get_draft(draft["id"], owner)["status"] == "saved"
    persisted = store.get_playlist(target["id"], owner)
    assert len(persisted["items"]) == 6
    queued = jobs.list_download_jobs(user_id=owner)
    assert len(queued) == 1
    assert queued[0]["target_playlist_id"] == target["id"]
    assert queued[0]["total_items"] == 7
    assert confirmed["protocol_status"] == "valid"
    episode = record_turn(confirmed, request, len(edited["messages"]))
    tasks = list_tasks(owner, "live-draft")
    assert len(tasks) == 1 and len(tasks[0]["episode_ids"]) == 3
    assert tasks[0]["entity_refs"]["job_id"] == [queued[0]["id"]]
    assert episode["result_status"] == "partial"  # queued is not downloaded
