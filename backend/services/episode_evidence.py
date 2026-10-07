"""Bounded, observable episode evidence; prose is never an execution receipt."""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

ENTITY_KEYS = {"bvid", "track_id", "song_id", "playlist_id", "target_playlist_id",
               "draft_id", "batch_id", "job_id", "action_id", "playback_session_id",
               "item_id", "filename", "local_file_path", "url"}
_OBJECT_IDS = {"track": "track_id", "tracks": "track_id", "playlist": "playlist_id", "draft": "draft_id",
               "job": "job_id", "download_job": "job_id", "batch": "batch_id"}
_FAILURES = {"error", "failed", "failure", "invalid", "unavailable", "not_found", "timeout", "blocked"}
_PENDING = {"queued", "running", "downloading", "pending", "issued", "dispatched", "confirming", "needs_download", "needs_confirmation"}
_SUCCESSES = {"ok", "success", "succeeded", "completed", "created", "saved", "added", "appended",
              "unchanged", "renamed", "reordered", "removed", "deleted", "draft", "empty", "cancelled", "downloaded",
              "presented", "ready", "no_results", "skipped", "promoted", "forgotten"}


def decode_payload(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def entity_refs(value: Any) -> dict[str, list[str]]:
    collected: dict[str, set[str]] = {}

    def add(key, value):
        if isinstance(value, (str, int)) and str(value).strip():
            collected.setdefault(key, set()).add(str(value)[:1000])

    def walk(raw, parent="", depth=0):
        if depth > 16:
            return
        raw = decode_payload(raw)
        if isinstance(raw, dict):
            if parent in _OBJECT_IDS:
                add(_OBJECT_IDS[parent], raw.get("id"))
            for key, nested in raw.items():
                entity_key = key[:-1] if key.endswith("s") and key[:-1] in ENTITY_KEYS else key
                if entity_key in ENTITY_KEYS:
                    for item in nested if isinstance(nested, list) else [nested]:
                        add(entity_key, item)
                walk(nested, key, depth + 1)
        elif isinstance(raw, list):
            for nested in raw[:100]:
                walk(nested, parent, depth + 1)
        elif isinstance(raw, str):
            for bvid in re.findall(r"BV[0-9A-Za-z]{10}", raw):
                add("bvid", bvid)
            for url in re.findall(r"https?://[^\s\]\[\"'<>]+", raw):
                add("url", url.rstrip(".,;，。"))

    walk(value)
    return {key: sorted(values)[:100] for key, values in sorted(collected.items())}


def compact_player_state(state: dict | None) -> dict:
    if not isinstance(state, dict):
        return {}
    aliases = {"current": ("current", "current_track", "currentTrack"),
               "playing": ("playing", "is_playing", "isPlaying"),
               "playback_mode": ("playback_mode", "playMode"),
               "playlist_id": ("playlist_id", "playlistId"),
               "playback_session_id": ("playback_session_id",),
               "current_item_id": ("current_item_id",), "available": ("available",),
               "progress": ("progress",), "duration": ("duration",), "volume": ("volume",)}
    result = {}
    for key, variants in aliases.items():
        for variant in variants:
            if variant in state:
                value = state[variant]
                if key == "current" and isinstance(value, dict):
                    value = {k: value[k] for k in ("id", "title", "author", "bvid") if k in value}
                result[key] = value
                break
    return result


def _receipt(name: str, payload: Any, phase="result") -> dict:
    payload = decode_payload(payload)
    if name == "execute_skill_script" and isinstance(payload, dict) and payload.get("status") == "completed":
        script_result = decode_payload(payload.get("stdout"))
        if isinstance(script_result, dict):
            return _receipt(name, script_result, phase)
    status = str(payload.get("status") or "").lower() if isinstance(payload, dict) else ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if status in _FAILURES or (isinstance(payload, dict) and payload.get("success") is False):
        outcome = "failed"
    elif status == "partial":
        outcome = "partial"
    elif status in _PENDING or status == "confirmation_required":
        outcome = "pending"
    elif status in _SUCCESSES:
        outcome = "succeeded"
    elif error:
        outcome = "failed"
    elif name in {"activate_skill", "read_skill_resource"} and isinstance(payload, str) and payload.strip():
        status, outcome = "read", "succeeded"
    elif isinstance(payload, dict) and name in {"local_search", "bili_search", "get_player_state", "list_music_playlists", "search_memory"}:
        status, outcome = "read", "succeeded"
    else:
        outcome = "unknown"
    return {"tool": name, "phase": phase, "status": status or "unknown", "outcome": outcome,
            "entity_refs": entity_refs(payload),
            "error_code": str(payload.get("code") or "")[:100] if error and isinstance(payload, dict) else ""}


def turn_evidence(events: list[dict], client_action_results: dict | None = None,
                  selected_tracks: list[dict] | None = None) -> dict:
    """Keep requested, candidate, selected and observed objects distinct."""
    receipts = []
    roles: dict[str, list[Any]] = {"requested": [], "candidates": [], "selected": selected_tracks or [],
                                 "draft_selection": [], "receipt_entities": [], "submitted": [], "downloaded": []}
    ack = client_action_results or {}
    for event in events:
        if event.get("phase") == "call":
            roles["requested"].append(event.get("input", {}))
        elif event.get("phase") == "result":
            payload = decode_payload(event.get("content", ""))
            receipt = _receipt(str(event.get("name") or "unknown"), payload)
            # A dispatched action is superseded only by its own ACK.
            action_id = payload.get("action_id") if isinstance(payload, dict) else None
            if action_id and action_id in ack:
                receipt = _receipt(receipt["tool"], ack[action_id], "ack")
                receipt["entity_refs"].setdefault("action_id", [action_id])
            receipts.append(receipt)
            if isinstance(payload, dict):
                if "draft" in payload:
                    roles["draft_selection"].append(payload["draft"].get("items", []))
                if "videos" in payload or "tracks" in payload:
                    roles["candidates"].append({k: payload[k] for k in ("videos", "tracks") if k in payload})
                if "batch" in payload:
                    roles["candidates"].append(payload["batch"])
                # Only top-level operation receipts, not nested search candidates.
                roles["receipt_entities"].append({k: payload[k] for k in ("playlist", "job", "download_job", "action_id", "draft_id", "batch_id") if k in payload})
                job = payload.get("job") or payload.get("download_job")
                if isinstance(job, dict) and receipt["outcome"] != "failed":
                    roles["submitted"].append(job.get("items", []))
                    roles["downloaded"].append([item for item in job.get("items", []) if item.get("status") == "downloaded"])
    calls = Counter(str(event.get("name")) for event in events if event.get("phase") == "call")
    results = Counter(str(event.get("name")) for event in events if event.get("phase") == "result")
    return {"receipts": receipts, "unreturned_tool_calls": list((calls - results).elements()),
            "entity_roles": {key: entity_refs({"tracks": values}) if key == "selected"
                                                  else entity_refs(values) for key, values in roles.items()}}


def episode_outcome(evidence: dict, *, interrupted: bool, had_calls: bool) -> str:
    outcomes = [receipt["outcome"] for receipt in evidence["receipts"]]
    success = "succeeded" in outcomes
    pending = "pending" in outcomes
    if evidence.get("unreturned_tool_calls"):
        return "partial" if success or pending else "failed"
    if interrupted:
        return "partial" if success or pending else "failed"
    if any(value in {"failed", "partial", "unknown"} for value in outcomes):
        return "partial" if success or pending or "partial" in outcomes else "failed"
    if pending:
        return "partial"
    if had_calls and not outcomes:
        return "failed"
    return "success"
