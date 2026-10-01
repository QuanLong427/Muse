"""Deterministic local-only candidates for playback Radio mode.

This is intentionally smaller than the future smart-playlist ranker. Radio
may extend the active PlaybackSession, but it never downloads remote media,
changes named playlists, or writes long-term preferences.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from services.music_library_store import list_feedback_excluded_track_ids, list_recent_tracks
from services.music_manager import scan_tracks


def recommend_local_radio_tracks(
    *,
    user_id: str,
    exclude_track_ids: list[str] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    excluded = {str(track_id) for track_id in (exclude_track_ids or []) if track_id}
    recent_ids = {
        str(entry["track"].get("id") or "")
        for entry in list_recent_tracks(user_id=user_id, limit=30)
    }
    feedback_excluded_ids = list_feedback_excluded_track_ids(user_id)
    candidates = [
        track
        for track in scan_tracks()
        if (
            track.id not in excluded
            and track.id not in recent_ids
            and track.id not in feedback_excluded_ids
        )
    ]
    rotation_key = datetime.now(timezone.utc).date().isoformat()
    candidates.sort(
        key=lambda track: hashlib.sha256(
            f"{user_id}:{rotation_key}:{track.id}".encode("utf-8")
        ).hexdigest()
    )
    selected = candidates[: max(1, min(int(limit), 20))]
    return {
        "tracks": [track.model_dump(mode="json") for track in selected],
        "reason_codes": [
            "local_only",
            "not_in_session",
            "not_recently_played",
            "negative_feedback_filtered",
        ],
        "excluded_count": len(excluded),
        "recent_excluded_count": len(recent_ids),
        "feedback_excluded_count": len(feedback_excluded_ids),
    }
