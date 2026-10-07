import json

import pytest
from models import Track
from services import playlist_draft_service as drafts, music_library_store as store, smart_playlist_service as smart, download_job_service as downloads


@pytest.fixture
def setup_draft(monkeypatch, tmp_path):
    for module in (store, downloads):
        monkeypatch.setattr(module, "_DB_DIR", tmp_path)
        monkeypatch.setattr(module, "_DB_PATH", tmp_path / f"{module.__name__}.db")
    local = Track(id="local.mp3", title="本地曲", author="歌手", filename="local.mp3", subDir="", date="", url="/api/tracks/local.mp3", size=1)
    other = local.model_copy(update={"id": "other.mp3", "title": "另一首"})
    lookup = lambda identity: {local.id: local, other.id: other}.get(identity)
    monkeypatch.setattr(drafts, "find_track_by_id", lookup)
    monkeypatch.setattr(drafts, "scan_tracks", lambda: [local, other])
    monkeypatch.setattr(smart, "find_track_by_id", lookup)
    monkeypatch.setattr(smart, "find_track_by_bvid", lambda bvid: None)

    def create(target=None, name="草稿", session="s"):
        constraints = {"target_playlist_id": target["id"] if target else "", "target_revision": target["revision"] if target else None}
        batch = store.record_recommendation_batch(user_id="u", kind="smart_playlist", scenario="默认", current_track_id=None, profile_snapshot={}, constraints=constraints,
            items=[{"track": local.model_dump()}, {"track": {"id": "bilibili:BV123", "bvid": "BV123", "title": "远程曲", "author": "", "video_title": "现场视频", "source_type": "bilibili"}}])
        return drafts.create_draft({"batch_id": batch["id"], "suggested_name": name}, user_id="u", session_id=session)
    return create, local, other


def test_edit_and_confirm_exact_selection_no_early_writes(setup_draft):
    create, _, other = setup_draft
    draft = create()
    assert store.list_playlists("u") == []
    assert downloads.list_download_jobs(user_id="u") == []
    original_batch = draft["batch_id"]
    draft = drafts.edit_draft(draft["id"], user_id="u", expected_revision=0, action="remove", item_ids=[draft["items"][1]["item_id"]])
    draft = drafts.edit_draft(draft["id"], user_id="u", expected_revision=1, action="replace", item_ids=[draft["items"][0]["item_id"]], track_id=other.id)
    assert len(store.get_recommendation_batch(original_batch, user_id="u")["items"]) == 2
    with pytest.raises(drafts.DraftConflictError):
        drafts.confirm_draft(draft["id"], user_id="u", expected_revision=0, schedule=False)
    saved = drafts.confirm_draft(draft["id"], user_id="u", expected_revision=2, schedule=False)
    assert saved["status"] == "saved"
    assert saved["receipt"]["items"][0]["track"]["id"] == other.id
    assert saved["receipt"]["download_job"] is None
    duplicate = drafts.confirm_draft(draft["id"], user_id="u", expected_revision=2, schedule=False)
    assert duplicate["receipt"]["id"] == saved["receipt"]["id"]
    with pytest.raises(drafts.DraftConflictError):
        drafts.edit_draft(draft["id"], user_id="u", expected_revision=2, action="rename", name="不能改")


def test_http_draft_edits_conflicts_and_confirmation(setup_draft, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import playlist_drafts as routes

    create, *_ = setup_draft
    draft = create()
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setattr(routes, "confirm_draft", lambda draft_id, **kwargs: drafts.confirm_draft(draft_id, schedule=False, **kwargs))
    client = TestClient(app)
    url = f"/api/playlist-drafts/{draft['id']}"
    assert client.get(url, params={"user_id": "other"}).status_code == 404
    assert client.get(url, params={"user_id": "u", "session_id": "wrong"}).status_code == 404
    response = client.patch(url, json={"user_id": "u", "expected_revision": 0, "action": "remove", "item_ids": [draft["items"][1]["item_id"]]})
    assert response.status_code == 200
    assert response.json()["revision"] == 1
    assert response.json()["items"][0]["position"] == 0
    assert store.list_playlists("u") == []
    assert client.post(url + "/confirm", json={"user_id": "u", "expected_revision": 0}).status_code == 409
    saved = client.post(url + "/confirm", json={"user_id": "u", "expected_revision": 1})
    assert saved.status_code == 200 and saved.json()["status"] == "saved"
    repeated = client.post(url + "/confirm", json={"user_id": "u", "expected_revision": 1})
    assert repeated.json()["receipt"]["id"] == saved.json()["receipt"]["id"]
    assert len(store.list_playlists("u")) == 1


def test_legacy_non_music_draft_rejected_before_freeze(setup_draft):
    create, *_ = setup_draft
    draft = create()
    with drafts._connect() as connection:
        draft["items"][1]["track"]["video_title"] = "ASMR 敲击音"
        connection.execute("UPDATE playlist_drafts SET payload_json=? WHERE id=?", (json.dumps(draft, ensure_ascii=False), draft["id"]))
    with pytest.raises(ValueError, match="非音乐"):
        drafts.confirm_draft(draft["id"], user_id="u", expected_revision=0, schedule=False)
    unchanged = drafts.get_draft(draft["id"], "u")
    assert unchanged["status"] == "draft" and unchanged["revision"] == 0
    assert store.list_playlists("u") == []
    assert downloads.list_download_jobs(user_id="u") == []
    assert drafts.edit_draft(draft["id"], user_id="u", expected_revision=0, action="remove", item_ids=[draft["items"][1]["item_id"]])["revision"] == 1



def test_mixed_confirmation_idempotent_owner_and_session(setup_draft):
    create, *_ = setup_draft
    draft = create()
    with pytest.raises(LookupError):
        drafts.get_draft(draft["id"], "other")
    with pytest.raises(LookupError):
        drafts.get_draft(draft["id"], "u", "other-session")
    first = drafts.confirm_draft(draft["id"], user_id="u", expected_revision=0, schedule=False)
    second = drafts.confirm_draft(draft["id"], user_id="u", expected_revision=0, schedule=False)
    assert len(store.list_playlists("u")) == 1
    assert len(downloads.list_download_jobs(user_id="u")) == 1
    assert first["receipt"]["download_job"]["id"] == second["receipt"]["download_job"]["id"]
    assert first["receipt"]["download_job"]["items"][0]["bvid"] == "BV123"


def test_ambiguous_confirmation_and_create_request_cannot_apply(setup_draft):
    create, *_ = setup_draft
    first, second = create(name="甲"), create(name="乙")
    for text in ("帮我创建歌单", "确认添加吗？", "不要确认添加", "确认添加"):
        assert not drafts.confirmation_allowed(text, first, [first, second])
    assert drafts.confirmation_allowed("确认添加甲", first, [first, second])
    assert not drafts.confirmation_allowed("确认添加乙", first, [first, second])
    assert drafts.confirmation_allowed("就这样，确认添加", first, [first])


def test_agent_guard_requires_current_human_confirmation(setup_draft):
    from services.ai_agent import _build_tools
    create, *_ = setup_draft
    draft = create(session="guard")
    def tool(message):
        return next(t for t in _build_tools(user_id="u", session_id="guard", current_request=message) if t.name == "manage_playlist_draft")
    arguments = {"action": "confirm", "draft_id": draft["id"], "expected_revision": 0}
    result = json.loads(tool("帮我创建歌单").invoke(arguments))
    assert result["status"] == "confirmation_required"
    assert store.list_playlists("u") == []


def test_qwen_json_string_array_edits_return_version_receipt(setup_draft):
    from services.ai_agent import _build_tools
    create, *_ = setup_draft
    draft = create(session="encoded")
    tool = next(t for t in _build_tools(user_id="u", session_id="encoded") if t.name == "manage_playlist_draft")
    result = json.loads(tool.invoke({"action": "remove", "draft_id": draft["id"], "expected_revision": 0,
        "item_ids": json.dumps([draft["items"][1]["item_id"]])}))
    assert result["previous_revision"] == 0
    assert result["draft"]["revision"] == 1
    assert len(result["draft"]["items"]) == 1
    assert downloads.list_download_jobs(user_id="u") == []


def test_confirming_freeze_survives_failure_and_retries_same_batch(setup_draft, monkeypatch):
    create, *_ = setup_draft
    draft = create()
    apply = drafts.save_smart_playlist_preview
    monkeypatch.setattr(drafts, "save_smart_playlist_preview", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("temporary failure")))
    with pytest.raises(RuntimeError):
        drafts.confirm_draft(draft["id"], user_id="u", expected_revision=0, schedule=False)
    assert drafts.get_draft(draft["id"], "u")["status"] == "confirming"
    with pytest.raises(drafts.DraftConflictError):
        drafts.edit_draft(draft["id"], user_id="u", expected_revision=0, action="rename", name="其他内容")
    monkeypatch.setattr(drafts, "save_smart_playlist_preview", apply)
    saved = drafts.confirm_draft(draft["id"], user_id="u", expected_revision=0, schedule=False)
    assert saved["batch_id"] == draft["batch_id"]


def test_append_target_revision_and_cancel_are_independent(setup_draft):
    create, local, *_ = setup_draft
    target = store.create_playlist("已有", user_id="u")
    draft = create(target)
    store.add_playlist_track(target["id"], track=local.model_dump(), expected_revision=None, user_id="u")
    with pytest.raises(store.PlaylistRevisionConflictError):
        drafts.confirm_draft(draft["id"], user_id="u", expected_revision=0, schedule=False)
    assert downloads.list_download_jobs(user_id="u") == []
    cancelled = create(name="取消")
    drafts.edit_draft(cancelled["id"], user_id="u", expected_revision=0, action="cancel")
    with pytest.raises(ValueError):
        drafts.confirm_draft(cancelled["id"], user_id="u", expected_revision=1, schedule=False)


def test_filter_uses_original_video_title_and_preserves_unchanged_items(setup_draft):
    create, *_ = setup_draft
    draft = create()
    local_id = draft["items"][0]["item_id"]
    edited = drafts.edit_draft(draft["id"], user_id="u", expected_revision=0, action="filter_versions", exclude_versions=["live"])
    assert len(edited["items"]) == 1
    assert edited["items"][0]["item_id"] == local_id
    assert edited["constraints"]["exclude_versions"] == ["live"]


def test_downloaded_items_follow_draft_order_preserving_other_playlist_items(setup_draft):
    create, local, other = setup_draft
    draft = create()
    draft = drafts.edit_draft(draft["id"], user_id="u", expected_revision=0, action="reorder", item_ids=[item["item_id"] for item in reversed(draft["items"])])
    saved = drafts.confirm_draft(draft["id"], user_id="u", expected_revision=1, schedule=False)
    target = saved["receipt"]
    remote = local.model_copy(update={"id": "downloaded.mp3", "bvid": "BV123"})
    store.add_playlist_track(target["id"], track=other.model_dump(), user_id="u", expected_revision=None)
    store.add_playlist_track(target["id"], track=remote.model_dump(), user_id="u", expected_revision=None)
    downloads._place_downloaded_items(saved["receipt"]["download_job"], {"BV123"})
    ordered = store.get_playlist(target["id"], "u")["items"]
    assert [item["track"]["id"] for item in ordered] == [remote.id, local.id, other.id]
