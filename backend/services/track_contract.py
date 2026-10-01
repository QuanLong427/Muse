"""Canonical presentation contracts for local and remote music candidates."""

from __future__ import annotations

import json
from typing import Any

from models import Track, TrackCard


def local_track_card(track: Track) -> TrackCard:
    return TrackCard(
        track_id=track.id,
        source_type="local",
        availability="local",
        title=track.title,
        author=track.author,
        bvid=track.bvid,
        url=track.url,
        download_status="downloaded",
        allowed_actions=["play", "add_to_session"],
        local_track=track,
    )


def remote_track_card(video: dict[str, Any]) -> TrackCard | None:
    bvid = str(video.get("bvid") or "").strip()
    title = str(video.get("title") or "").strip()
    if not bvid or not title:
        return None
    return TrackCard(
        track_id=f"bilibili:{bvid}",
        source_type="bilibili",
        availability="remote",
        title=title,
        author=str(video.get("author") or ""),
        duration=str(video.get("duration") or ""),
        bvid=bvid,
        url=str(video.get("url") or f"https://www.bilibili.com/video/{bvid}"),
        download_status="idle",
        allowed_actions=["download"],
    )


def track_cards_from_tool_result(tool_name: str, raw: Any) -> list[dict[str, Any]]:
    """Convert known tool results to cards without asking the model to rewrite them."""
    content = getattr(raw, "content", raw)
    if not isinstance(content, str):
        return []
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    cards: list[TrackCard] = []
    if tool_name == "present_tracks":
        raw_cards = payload.get("tracks", [])
        if isinstance(raw_cards, list):
            for item in raw_cards:
                if not isinstance(item, dict):
                    continue
                try:
                    cards.append(TrackCard.model_validate(item))
                except (TypeError, ValueError):
                    continue
    elif tool_name == "convert_video":
        raw_tracks = payload.get("tracks", [])
        if isinstance(raw_tracks, list):
            for item in raw_tracks:
                if not isinstance(item, dict):
                    continue
                try:
                    cards.append(local_track_card(Track.model_validate(item)))
                except (TypeError, ValueError):
                    continue
    return [card.model_dump(mode="json") for card in cards]
