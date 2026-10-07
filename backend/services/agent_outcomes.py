"""Evidence-only reporting when an agent turn cannot finish normally."""

from __future__ import annotations

import json
from typing import Any


class ExecutionBudgetExceeded(Exception):
    """The application stops planning before the graph's hard recursion limit."""


def observed_progress(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collect durable write receipts, never equating a search with a write."""
    playlists: dict[str, Any] = {}
    jobs: dict[str, Any] = {}
    for event in events:
        if event.get("phase") != "result":
            continue
        name = event.get("name")
        if name not in {"create_music_playlist", "add_track_to_music_playlist", "manage_music_playlist", "create_smart_playlist", "manage_playlist_draft", "convert_video"}:
            continue
        try:
            payload = json.loads(event.get("content") or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("status") == "deleted":
            playlists.pop(str(payload.get("playlist_id") or ""), None)
        if payload.get("status") in {"created", "saved", "added", "appended", "unchanged", "queued", "renamed", "reordered", "removed"}:
            playlist = payload.get("playlist")
            if isinstance(playlist, dict) and playlist.get("id"):
                playlists[str(playlist["id"])] = playlist
        job = payload.get("job")
        if isinstance(job, dict) and job.get("id"):
            jobs[str(job["id"])] = job
    return {"playlists": playlists, "jobs": jobs}


def interrupted_response(events: list[dict[str, Any]], *, error: Exception) -> str:
    """Build a bounded, credential-free report from actual observations only."""
    reason = "执行步数达到上限" if isinstance(error, ExecutionBudgetExceeded) or type(error).__name__ == "GraphRecursionError" else "执行过程中发生异常"
    lines = [f"本轮操作未全部完成：{reason}，已停止新增操作。"]
    progress = observed_progress(events)
    for playlist in progress["playlists"].values():
        lines.append(f"已观察到歌单「{str(playlist.get('name') or '未命名')[:100]}」保存成功，最后确认包含 {len(playlist.get('items') or [])} 首本地歌曲；这些修改未回滚。")
    for job in progress["jobs"].values():
        completed = int(job.get("completed_items") or 0)
        total = int(job.get("total_items") or 0)
        lines.append(f"下载任务 {str(job['id'])[:8]} 已创建，最后确认状态为 {job.get('status') or '未知'}，完成 {completed}/{total} 首；请在“下载任务”查看最新结果。")
    if not progress["jobs"]:
        lines.append("本轮未观察到下载任务创建成功，不能视为联网歌曲已下载。")
    if not progress["playlists"] and not progress["jobs"]:
        lines.append("没有已确认的歌单保存或下载任务结果；工具调用意图不代表执行成功。")
    lines.append("剩余目标尚未确认完成，没有自动重复执行写入。")
    return "\n\n".join(lines)
