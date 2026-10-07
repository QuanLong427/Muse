from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.music_library_store import (
    DEFAULT_USER_ID,
    PlaylistRevisionConflictError,
    add_playlist_track,
    create_playlist,
    delete_playlist,
    get_playlist,
    get_recommendation_batch,
    get_track_feedback_summary,
    list_playlists,
    list_recent_tracks,
    record_playback_event,
    record_track_feedback,
    remove_playlist_item,
    reorder_playlist_items,
    update_playlist,
)
from services.music_manager import find_track_by_id


router = APIRouter(tags=["music-library"])


class CreatePlaylistRequest(BaseModel):
    user_id: str = DEFAULT_USER_ID
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class UpdatePlaylistRequest(CreatePlaylistRequest):
    expected_revision: int | None = Field(default=None, ge=0)


class AddPlaylistTrackRequest(BaseModel):
    user_id: str = DEFAULT_USER_ID
    track_id: str = Field(min_length=1, max_length=1024)
    expected_revision: int | None = Field(default=None, ge=0)


class ReorderPlaylistRequest(BaseModel):
    user_id: str = DEFAULT_USER_ID
    item_ids: list[str] = Field(max_length=5000)
    expected_revision: int | None = Field(default=None, ge=0)


class PlaybackEventRequest(BaseModel):
    user_id: str = DEFAULT_USER_ID
    session_id: str = Field(min_length=1, max_length=256)
    item_id: str | None = None
    event_type: Literal[
        "play_started", "play_resumed", "play_paused", "play_completed", "play_skipped", "play_stopped"
    ]
    track: dict
    position_seconds: float = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    origin_type: str = Field(default="manual", max_length=64)
    origin_id: str | None = Field(default=None, max_length=256)
    scenario: str = Field(default="默认", max_length=80)


class RadioRecommendationRequest(BaseModel):
    user_id: str = DEFAULT_USER_ID
    exclude_track_ids: list[str] = Field(default_factory=list, max_length=5000)
    limit: int = Field(default=5, ge=1, le=20)
    scenario: str = Field(default="默认", max_length=80)
    current_track_id: str | None = Field(default=None, max_length=1024)


class TrackFeedbackRequest(BaseModel):
    user_id: str = DEFAULT_USER_ID
    track_id: str = Field(min_length=1, max_length=1024)
    feedback_type: Literal[
        "like",
        "dislike",
        "dislike_version",
        "not_now",
        "more_like_this",
        "replay",
        "favorite",
    ]
    scenario: str = Field(default="默认", max_length=80)
    source: str = Field(default="user", max_length=40)


class SmartPlaylistPreviewRequest(BaseModel):
    target_playlist_id: str = Field(default="", max_length=128)
    user_id: str = DEFAULT_USER_ID
    scenario: str = Field(default="默认", max_length=80)
    count: int | None = Field(default=None, ge=1, le=50)
    duration_minutes: float | None = Field(default=None, ge=1, le=720)
    query: str = Field(default="", max_length=500)
    include_artists: list[str] = Field(default_factory=list, max_length=30)
    exclude_artists: list[str] = Field(default_factory=list, max_length=30)
    genre: str = Field(default="", max_length=80)
    mood: str = Field(default="", max_length=80)
    language: str = Field(default="", max_length=40)
    exclude_versions: list[str] = Field(default_factory=list, max_length=10)
    energy_curve: str = Field(default="", max_length=40)
    source_policy: Literal["balanced", "local", "cloud"] = "balanced"


class SaveSmartPlaylistRequest(BaseModel):
    target_playlist_id: str = Field(default="", max_length=128)
    user_id: str = DEFAULT_USER_ID
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


def _translate_error(exc: Exception):
    if isinstance(exc, PlaylistRevisionConflictError):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "revision_conflict",
                "expected_revision": exc.expected,
                "actual_revision": exc.actual,
            },
        ) from exc
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.get("/api/playlists")
async def read_playlists(user_id: str = Query(DEFAULT_USER_ID, min_length=1, max_length=128)):
    return {"playlists": list_playlists(user_id)}


@router.post("/api/playlists", status_code=201)
async def write_playlist(req: CreatePlaylistRequest):
    try:
        return create_playlist(req.name, req.description, req.user_id)
    except Exception as exc:
        _translate_error(exc)


@router.get("/api/playlists/{playlist_id}")
async def read_playlist(playlist_id: str, user_id: str = Query(DEFAULT_USER_ID)):
    playlist = get_playlist(playlist_id, user_id)
    if playlist is None:
        raise HTTPException(status_code=404, detail="playlist not found")
    return playlist


@router.patch("/api/playlists/{playlist_id}")
async def patch_playlist(playlist_id: str, req: UpdatePlaylistRequest):
    try:
        return update_playlist(
            playlist_id,
            name=req.name,
            description=req.description,
            expected_revision=req.expected_revision,
            user_id=req.user_id,
        )
    except Exception as exc:
        _translate_error(exc)


@router.delete("/api/playlists/{playlist_id}")
async def remove_playlist(
    playlist_id: str,
    user_id: str = Query(DEFAULT_USER_ID),
    expected_revision: int | None = Query(default=None, ge=0),
):
    try:
        delete_playlist(
            playlist_id,
            expected_revision=expected_revision,
            user_id=user_id,
        )
        return {"status": "deleted", "id": playlist_id}
    except Exception as exc:
        _translate_error(exc)


@router.post("/api/playlists/{playlist_id}/items")
async def write_playlist_item(playlist_id: str, req: AddPlaylistTrackRequest):
    track = find_track_by_id(req.track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="local track not found")
    try:
        return add_playlist_track(
            playlist_id,
            track=track.model_dump(mode="json"),
            expected_revision=req.expected_revision,
            user_id=req.user_id,
        )
    except Exception as exc:
        _translate_error(exc)


@router.put("/api/playlists/{playlist_id}/items")
async def reorder_playlist(playlist_id: str, req: ReorderPlaylistRequest):
    try:
        return reorder_playlist_items(
            playlist_id,
            req.item_ids,
            expected_revision=req.expected_revision,
            user_id=req.user_id,
        )
    except Exception as exc:
        _translate_error(exc)


@router.delete("/api/playlists/{playlist_id}/items/{item_id}")
async def remove_item(
    playlist_id: str,
    item_id: str,
    user_id: str = Query(DEFAULT_USER_ID),
    expected_revision: int | None = Query(default=None, ge=0),
):
    try:
        return remove_playlist_item(
            playlist_id,
            item_id,
            expected_revision=expected_revision,
            user_id=user_id,
        )
    except Exception as exc:
        _translate_error(exc)


@router.post("/api/playback-events", status_code=201)
async def write_playback_event(req: PlaybackEventRequest):
    track_id = str(req.track.get("id") or "")
    track = find_track_by_id(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="local track not found")
    payload = req.model_dump(mode="json")
    payload["track"] = track.model_dump(mode="json")
    return record_playback_event(**payload)


@router.get("/api/playback-events/recent")
async def read_recent_tracks(
    user_id: str = Query(DEFAULT_USER_ID, min_length=1, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
):
    recent = []
    for entry in list_recent_tracks(user_id, limit):
        track = find_track_by_id(str(entry["track"].get("id") or ""))
        if track is None:
            continue
        recent.append({**entry, "track": track.model_dump(mode="json")})
    return {"tracks": recent}


@router.post("/api/recommendations/radio")
async def recommend_radio(req: RadioRecommendationRequest):
    from services.recommendation_service import recommend_local_radio_tracks

    return recommend_local_radio_tracks(
        user_id=req.user_id,
        exclude_track_ids=req.exclude_track_ids,
        limit=req.limit,
        scenario=req.scenario,
        current_track_id=req.current_track_id,
    )


@router.post("/api/smart-playlists/preview")
def preview_smart_playlist(req: SmartPlaylistPreviewRequest):
    from services.smart_playlist_service import generate_smart_playlist

    try:
        return generate_smart_playlist(**req.model_dump(mode="python"))
    except Exception as exc:
        _translate_error(exc)


@router.post("/api/smart-playlists/{batch_id}/save", status_code=201)
def save_smart_playlist(batch_id: str, req: SaveSmartPlaylistRequest):
    from services.smart_playlist_service import save_smart_playlist_preview

    try:
        return save_smart_playlist_preview(
            batch_id=batch_id,
            name=req.name,
            description=req.description,
            user_id=req.user_id,
            target_playlist_id=req.target_playlist_id,
        )
    except Exception as exc:
        _translate_error(exc)


@router.get("/api/recommendations/{batch_id}")
async def read_recommendation_batch(
    batch_id: str,
    user_id: str = Query(DEFAULT_USER_ID, min_length=1, max_length=128),
):
    batch = get_recommendation_batch(batch_id, user_id=user_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="recommendation batch not found")
    return batch


@router.get("/api/preferences/recent")
async def read_recent_preferences(
    user_id: str = Query(DEFAULT_USER_ID, min_length=1, max_length=128),
    window_days: int = Query(default=7),
):
    from services.preference_service import (
        build_recent_preference_profile,
        get_preference_window,
    )

    if window_days not in {7, 30}:
        raise HTTPException(status_code=422, detail="window_days must be 7 or 30")
    profile = build_recent_preference_profile(user_id)
    return {
        "user_id": profile["user_id"],
        "generated_at": profile["generated_at"],
        "policy_version": profile["policy_version"],
        "window": get_preference_window(profile, window_days),
    }


@router.post("/api/track-feedback", status_code=201)
async def write_track_feedback(req: TrackFeedbackRequest):
    if find_track_by_id(req.track_id) is None:
        raise HTTPException(status_code=404, detail="local track not found")
    try:
        return record_track_feedback(**req.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/track-feedback")
async def read_track_feedback(
    user_id: str = Query(DEFAULT_USER_ID, min_length=1, max_length=128),
    track_id: str | None = Query(default=None, max_length=1024),
):
    return {"feedback": get_track_feedback_summary(user_id=user_id, track_id=track_id)}
