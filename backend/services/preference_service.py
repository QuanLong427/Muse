"""Deterministic short-term preference projection from append-only evidence.

This module does not write durable user memories. It derives a disposable
7/30-day view from playback and explicit-feedback events so recommendation
features can share one auditable scoring policy.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from models import Track
from services.music_metadata import normalize_artist
from services.music_library_store import (
    DEFAULT_USER_ID,
    list_playback_events_since,
    list_track_feedback_events_since,
)
from services.music_manager import scan_tracks


PREFERENCE_WINDOWS = (7, 30)
_FEEDBACK_WEIGHTS = {
    "like": 4.0,
    "favorite": 5.0,
    "more_like_this": 4.0,
    "replay": 2.5,
    "dislike": -6.0,
    "dislike_version": -5.0,
    "not_now": -1.5,
}


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decay(age_days: float, window_days: int) -> float:
    half_life = max(1.0, window_days / 2)
    return math.pow(0.5, max(0.0, age_days) / half_life)


def _playback_weight(event: dict[str, Any]) -> float:
    event_type = str(event.get("event_type") or "")
    if event_type == "play_completed":
        return 2.5
    if event_type == "play_started":
        return 0.2
    if event_type == "play_resumed":
        return 0.05
    if event_type not in {"play_skipped", "play_stopped"}:
        return 0.0
    duration = max(0.0, float(event.get("duration_seconds") or 0.0))
    position = max(0.0, float(event.get("position_seconds") or 0.0))
    ratio = position / duration if duration > 0 else 0.0
    if event_type == "play_skipped":
        return -1.2 if ratio < 0.25 else -0.35
    return -0.75 if ratio < 0.25 else -0.1


def _empty_signal(track_id: str, metadata: dict[str, str]) -> dict[str, Any]:
    return {
        "track_id": track_id,
        "title": metadata.get("title", ""),
        "author": normalize_artist(metadata.get("author", "")),
        "score": 0.0,
        "evidence_count": 0,
        "positive_count": 0,
        "negative_count": 0,
        "latest_at": "",
        "source_event_ids": [],
    }


def _window_projection(
    *,
    days: int,
    now: datetime,
    playback_events: list[dict[str, Any]],
    feedback_events: list[dict[str, Any]],
    catalog: dict[str, dict[str, str]],
) -> dict[str, Any]:
    cutoff = now - timedelta(days=days)
    tracks: dict[str, dict[str, Any]] = {}
    scenario_scores: dict[str, float] = defaultdict(float)

    def add_signal(
        track_id: str,
        *,
        weight: float,
        occurred_at: datetime,
        metadata: dict[str, str],
        scenario: str | None = None,
        source_id: str = "",
    ) -> None:
        if not track_id or weight == 0 or occurred_at < cutoff:
            return
        decayed = weight * _decay((now - occurred_at).total_seconds() / 86400, days)
        signal = tracks.setdefault(track_id, _empty_signal(track_id, metadata))
        if not signal["title"]:
            signal["title"] = metadata.get("title", "")
        if not signal["author"]:
            signal["author"] = normalize_artist(metadata.get("author", ""))
        signal["score"] += decayed
        signal["evidence_count"] += 1
        signal["positive_count" if weight > 0 else "negative_count"] += 1
        iso_time = occurred_at.isoformat()
        if iso_time > signal["latest_at"]:
            signal["latest_at"] = iso_time
        if source_id and source_id not in signal["source_event_ids"]:
            signal["source_event_ids"].append(source_id)
        if scenario:
            scenario_scores[scenario] += decayed

    for event in playback_events:
        occurred_at = _parse_time(event.get("occurred_at"))
        if occurred_at is None:
            continue
        track_id = str(event.get("track_id") or "")
        metadata = {
            "title": str(event.get("title") or ""),
            "author": str(event.get("author") or ""),
        }
        add_signal(
            track_id,
            weight=_playback_weight(event),
            occurred_at=occurred_at,
            metadata=metadata,
            scenario=str(event.get("scenario") or "默认"),
            source_id=str(event.get("id") or ""),
        )

    for event in feedback_events:
        occurred_at = _parse_time(event.get("occurred_at"))
        if occurred_at is None:
            continue
        track_id = str(event.get("track_id") or "")
        add_signal(
            track_id,
            weight=_FEEDBACK_WEIGHTS.get(str(event.get("feedback_type") or ""), 0.0),
            occurred_at=occurred_at,
            metadata=catalog.get(track_id, {}),
            scenario=str(event.get("scenario") or "默认"),
            source_id=str(event.get("id") or ""),
        )

    artist_values: dict[str, dict[str, Any]] = {}
    for signal in tracks.values():
        signal["score"] = round(float(signal["score"]), 6)
        author = normalize_artist(signal.get("author"))
        if not author:
            continue
        artist = artist_values.setdefault(
            author,
            {
                "author": author,
                "score": 0.0,
                "track_count": 0,
                "evidence_count": 0,
                "source_event_ids": [],
            },
        )
        artist["score"] += float(signal["score"])
        artist["track_count"] += 1
        artist["evidence_count"] += int(signal["evidence_count"])
        artist["source_event_ids"] = sorted(
            set(artist["source_event_ids"]).union(signal["source_event_ids"])
        )

    ranked_tracks = sorted(
        tracks.values(),
        key=lambda item: (-float(item["score"]), -int(item["evidence_count"]), item["track_id"]),
    )
    ranked_artists = sorted(
        (
            {**item, "score": round(float(item["score"]), 6)}
            for item in artist_values.values()
        ),
        key=lambda item: (-float(item["score"]), -int(item["evidence_count"]), item["author"]),
    )
    return {
        "days": days,
        "cutoff": cutoff.isoformat(),
        "playback_event_count": sum(
            1 for item in playback_events if (_parse_time(item.get("occurred_at")) or cutoff) >= cutoff
        ),
        "feedback_event_count": sum(
            1 for item in feedback_events if (_parse_time(item.get("occurred_at")) or cutoff) >= cutoff
        ),
        "tracks": ranked_tracks[:20],
        "artists": ranked_artists[:20],
        "scenarios": [
            {"scenario": name, "score": round(score, 6)}
            for name, score in sorted(
                scenario_scores.items(), key=lambda item: (-item[1], item[0])
            )
        ],
    }


def build_recent_preference_profile(
    user_id: str = DEFAULT_USER_ID,
    *,
    now: datetime | None = None,
    catalog_tracks: list[Track] | None = None,
    scenario: str | None = None,
) -> dict[str, Any]:
    """Build 7/30-day projections without mutating long-term memory.

    When ``scenario`` is provided, only evidence produced in that scenario is
    included.  This keeps conversational recommendations scenario-aware while
    preserving the existing all-scenario projection for general reporting.
    """
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    oldest = current - timedelta(days=max(PREFERENCE_WINDOWS))
    playback_events = list_playback_events_since(
        user_id=user_id, since=oldest.isoformat()
    )
    feedback_events = list_track_feedback_events_since(
        user_id=user_id, since=oldest.isoformat()
    )
    scenario_filter = (scenario or "").strip()
    if scenario_filter:
        playback_events = [
            item
            for item in playback_events
            if str(item.get("scenario") or "默认") == scenario_filter
        ]
        feedback_events = [
            item
            for item in feedback_events
            if str(item.get("scenario") or "默认") == scenario_filter
        ]
    catalog = {
        track.id: {"title": track.title, "author": track.author}
        for track in (catalog_tracks if catalog_tracks is not None else scan_tracks())
    }
    return {
        "user_id": user_id.strip() or DEFAULT_USER_ID,
        "generated_at": current.isoformat(),
        "policy_version": "recent-preference-v1",
        "scenario": scenario_filter or None,
        "windows": [
            _window_projection(
                days=days,
                now=current,
                playback_events=playback_events,
                feedback_events=feedback_events,
                catalog=catalog,
            )
            for days in PREFERENCE_WINDOWS
        ],
    }


def get_preference_window(profile: dict[str, Any], days: int = 7) -> dict[str, Any]:
    windows = profile.get("windows")
    if not isinstance(windows, list):
        return {"days": days, "tracks": [], "artists": [], "scenarios": []}
    return next(
        (item for item in windows if isinstance(item, dict) and item.get("days") == days),
        {"days": days, "tracks": [], "artists": [], "scenarios": []},
    )
