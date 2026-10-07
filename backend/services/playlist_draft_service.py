"""Editable chat drafts; immutable selection batches and explicit application receipts.

A draft never owns playback or audio. Every edit creates an audited selection
snapshot. Confirmation freezes that snapshot before invoking idempotent playlist
and download services, so crash recovery cannot apply a different selection.
"""
from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from services import music_library_store as store
from services.music_manager import find_track_by_id, scan_tracks
from services.smart_playlist_service import generate_smart_playlist, save_smart_playlist_preview

_LOCK = threading.RLock()


class DraftConflictError(ValueError):
    pass


def confirmation_allowed(message: str, draft: dict, drafts: list[dict]) -> bool:
    """Conservative confirmation gate grounded in the current human message."""
    if re.search(r"不要|先别|不确认|不保存|不添加|取消|暂不|别保存|别添加|吗|么|？|\?", message):
        return False
    if not re.search(r"确认(?:添加|保存|创建)|就这样[，,\s]*(?:添加|保存)|保存这份|添加这份", message):
        return False
    pending = [item for item in drafts if item["status"] in {"draft", "confirming"}]
    if len(pending) > 1:
        selected = [item for item in pending if item["id"] in message or (item["name"] and item["name"] in message)]
        return len(selected) == 1 and selected[0]["id"] == draft["id"]
    return draft["status"] in {"draft", "confirming"}


@contextmanager
def _connect():
    store.init_music_library_db()
    conn = store._get_conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS playlist_drafts (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
        revision INTEGER NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.commit()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _read(conn, draft_id: str, user_id: str, session_id: str | None = None):
    row = conn.execute("SELECT * FROM playlist_drafts WHERE id=? AND user_id=?", (draft_id, user_id)).fetchone()
    if row is None or (session_id is not None and row["session_id"] != session_id):
        raise LookupError("歌单草稿不存在或不属于当前会话")
    return {**json.loads(row["payload_json"]), "id": row["id"], "revision": row["revision"], "status": row["status"]}


def get_draft(draft_id: str, user_id: str, session_id: str | None = None) -> dict:
    with _connect() as conn:
        draft = _read(conn, draft_id, user_id, session_id)
    receipt = draft.get("receipt")
    if receipt and receipt.get("download_job"):
        from services.download_job_service import get_download_job
        job = get_download_job(receipt["download_job"]["id"], user_id=user_id)
        if job:
            draft["receipt"] = {**receipt, "download_job": job}
    # Availability is a live projection, not a property of an immutable candidate.
    catalog = scan_tracks()
    by_id = {track.id: track for track in catalog}
    by_bvid = {track.bvid: track for track in catalog if track.bvid}
    local_tracks = {}
    for item in draft["items"]:
        raw = item["track"]
        canonical = by_id.get(raw["id"]) or by_bvid.get(raw.get("bvid"))
        if canonical:
            local_tracks[raw["id"]] = canonical.model_dump(mode="json")
    draft["local_tracks"] = local_tracks
    return draft


def list_drafts(user_id: str, session_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT id FROM playlist_drafts WHERE user_id=? AND session_id=? ORDER BY created_at DESC LIMIT 10", (user_id, session_id)).fetchall()
    return [get_draft(row["id"], user_id, session_id) for row in rows]


def create_draft(preview: dict, *, user_id: str, session_id: str, name: str = "") -> dict:
    batch = store.get_recommendation_batch(str(preview.get("batch_id") or ""), user_id=user_id)
    if batch is None or batch["kind"] != "smart_playlist":
        raise ValueError("没有可用于创建草稿的智能歌单预览")
    target_id = str(batch["constraints"].get("target_playlist_id") or "")
    target = store.get_playlist(target_id, user_id) if target_id else None
    draft = {
        "id": str(uuid4()), "user_id": user_id, "session_id": session_id,
        "revision": 0, "status": "draft", "batch_id": batch["id"],
        "name": (target["name"] if target else name.strip() or preview.get("suggested_name") or "智能歌单")[:100],
        "target_playlist_id": target_id, "scenario": batch["scenario"],
        "constraints": batch["constraints"], "warnings": preview.get("warnings") or [],
        "items": [{**item, "item_id": str(uuid4())} for item in batch["items"]],
        "receipt": None, "last_error": "",
    }
    now = store._now()
    with _connect() as conn:
        conn.execute("INSERT INTO playlist_drafts VALUES (?, ?, ?, 0, 'draft', ?, ?, ?)", (draft["id"], user_id, session_id, json.dumps(draft, ensure_ascii=False), now, now))
    return draft


def _check_revision(draft: dict, revision: int):
    if draft["revision"] != revision:
        raise DraftConflictError("草稿已更新，请刷新后确认最新版本")


def edit_draft(draft_id: str, *, user_id: str, expected_revision: int,
               action: str, name: str = "", item_ids: list[str] | None = None,
               track_id: str = "", candidate_batch_id: str = "", candidate_track_id: str = "",
               exclude_versions: list[str] | None = None, session_id: str | None = None,
               cloud_search=None) -> dict:
    with _LOCK:
        draft = get_draft(draft_id, user_id, session_id)
        draft.pop("local_tracks", None)
        _check_revision(draft, expected_revision)
        if draft["status"] != "draft":
            raise DraftConflictError("草稿已确认，不能再修改此次提交")
        items = list(draft["items"])
        ids = item_ids or []
        known_ids = {item["item_id"] for item in items}
        if any(item_id not in known_ids for item_id in ids):
            raise ValueError("草稿中没有该歌曲条目")
        if action == "rename":
            if draft["target_playlist_id"]:
                raise ValueError("追加草稿不能改名已有歌单")
            if not name.strip() or len(name.strip()) > 100:
                raise ValueError("歌单名称应为 1-100 个字符")
            draft["name"] = name.strip()
        elif action == "remove":
            if not ids:
                raise ValueError("请选择要移除的歌曲")
            items = [item for item in items if item["item_id"] not in ids]
        elif action == "reorder":
            if len(ids) != len(items) or set(ids) != known_ids:
                raise ValueError("重排必须包含全部条目，且每项一次")
            by_id = {item["item_id"]: item for item in items}
            items = [by_id[item_id] for item_id in ids]
        elif action == "filter_versions":
            from models import Track
            from services.smart_playlist_service import _matches_version, _normalized_versions
            versions = _normalized_versions(exclude_versions)
            draft["constraints"]["exclude_versions"] = list(dict.fromkeys([*draft["constraints"].get("exclude_versions", []), *versions]))
            items = [item for item in items if not _matches_version(Track(id=item["track"]["id"], title=item["track"]["title"], author="", filename=f"{item['track'].get('filename', '')} {item['track'].get('video_title', '')}", date="", subDir="", size=0, url=""), versions)]
        elif action in {"add", "replace"}:
            if action == "replace" and len(ids) != 1:
                raise ValueError("替换需要一个明确条目")
            candidate = None
            if track_id:
                track = find_track_by_id(track_id)
                if track is None:
                    raise LookupError("本地歌曲已不存在")
                candidate = {"track": track.model_dump(mode="json"), "score": 0, "reasons": [{"detail": "用户手动选择", "code": "manual_selection"}]}
            else:
                if candidate_batch_id:
                    batch = store.get_recommendation_batch(candidate_batch_id, user_id=user_id)
                    candidates = batch["items"] if batch else []
                else:
                    allowed = {key: value for key, value in draft["constraints"].items() if key in {
                        "query", "include_artists", "exclude_artists", "genre", "mood", "language", "exclude_versions", "energy_curve", "source_policy"}}
                    preview = generate_smart_playlist(user_id=user_id, scenario=draft["scenario"],
                        count=min(50, len(items) + 5), target_playlist_id=draft["target_playlist_id"], cloud_search=cloud_search, **allowed)
                    candidates = preview["recommendations"]
                    draft["warnings"] = list({w["code"]: w for w in [*draft["warnings"], *preview["warnings"]]}.values())
                used = {item["track"]["id"] for item in items}
                used_bvids = {item["track"].get("bvid") for item in items if item["track"].get("bvid")}
                candidate = next((item for item in candidates if item["track"]["id"] not in used
                    and item["track"].get("bvid") not in used_bvids
                    and (not candidate_track_id or item["track"]["id"] == candidate_track_id)), None)
            if candidate is None:
                raise ValueError("没有符合条件的新候选，草稿未修改")
            new_item = {**candidate, "item_id": str(uuid4())}
            if any(item["track"]["id"] == candidate["track"]["id"] or (candidate["track"].get("bvid") and item["track"].get("bvid") == candidate["track"]["bvid"]) for item in items):
                raise ValueError("歌曲已经在草稿中")
            if action == "replace":
                items = [new_item if item["item_id"] == ids[0] else item for item in items]
            else:
                items.append(new_item)
        elif action == "cancel":
            draft["status"] = "cancelled"
        else:
            raise ValueError("不支持的草稿操作")
        if len(items) > 50:
            raise ValueError("草稿最多 50 首歌曲")
        items = [{**item, "position": position} for position, item in enumerate(items)]
        draft["items"] = items
        if items and action != "cancel":
            previous = store.get_recommendation_batch(draft["batch_id"], user_id=user_id)
            snapshot = store.record_recommendation_batch(user_id=user_id, kind="smart_playlist", scenario=draft["scenario"],
                current_track_id=None, profile_snapshot=previous["profile"], constraints=draft["constraints"], items=items)
            draft["batch_id"] = snapshot["id"]
        draft["revision"] += 1
        with _connect() as conn:
            changed = conn.execute("UPDATE playlist_drafts SET revision=?, status=?, payload_json=?, updated_at=? WHERE id=? AND user_id=? AND revision=? AND status='draft'",
                (draft["revision"], draft["status"], json.dumps(draft, ensure_ascii=False), store._now(), draft_id, user_id, expected_revision)).rowcount
            if not changed:
                raise DraftConflictError("草稿已被其他操作更新")
        return draft


def confirm_draft(draft_id: str, *, user_id: str, expected_revision: int,
                  session_id: str | None = None, schedule: bool = True) -> dict:
    with _LOCK:
        draft = get_draft(draft_id, user_id, session_id)
        draft.pop("local_tracks", None)
        _check_revision(draft, expected_revision)
        if draft["status"] == "saved":
            return draft
        if draft["status"] not in {"draft", "confirming"} or not draft["items"]:
            raise ValueError("当前草稿不能确认保存")
        from services.music_metadata import is_non_music_source
        if any(item["track"].get("source_type") == "bilibili" and is_non_music_source(str(item["track"].get("video_title") or item["track"].get("title") or "")) for item in draft["items"]):
            raise ValueError("草稿包含非音乐联网候选，请先移除或替换后再确认")
        # Persist the freeze BEFORE external writes. A retry applies the same batch.
        with _connect() as conn:
            changed = conn.execute("UPDATE playlist_drafts SET status='confirming', updated_at=? WHERE id=? AND user_id=? AND revision=? AND status IN ('draft','confirming')", (store._now(), draft_id, user_id, expected_revision)).rowcount
            if not changed:
                raise DraftConflictError("草稿已被其他操作更新")
        try:
            receipt = save_smart_playlist_preview(batch_id=draft["batch_id"], name=draft["name"],
                user_id=user_id, target_playlist_id=draft["target_playlist_id"], schedule=schedule)
        except Exception as exc:
            draft["last_error"] = str(exc)[:500]
            with _connect() as conn:
                conn.execute("UPDATE playlist_drafts SET payload_json=? WHERE id=?", (json.dumps(draft, ensure_ascii=False), draft_id))
            raise
        draft.update(status="saved", receipt=receipt, last_error="")
        with _connect() as conn:
            conn.execute("UPDATE playlist_drafts SET status='saved', payload_json=?, updated_at=? WHERE id=?", (json.dumps(draft, ensure_ascii=False), store._now(), draft_id))
        return draft
