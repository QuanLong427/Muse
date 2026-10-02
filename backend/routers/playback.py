from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.playback_session_store import (
    DEFAULT_USER_ID,
    RevisionConflictError,
    get_playback_session,
    replace_playback_session,
)


router = APIRouter(prefix="/api/playback-session", tags=["playback"])


class PlaybackSessionItemRequest(BaseModel):
    id: str | None = None
    track: dict
    origin_type: Literal[
        "manual", "playlist", "smart_playlist", "agent", "radio", "recommendation", "legacy"
    ] = "manual"
    origin_id: str | None = None
    added_at: str | None = None


class PlaybackSessionRequest(BaseModel):
    user_id: str = DEFAULT_USER_ID
    expected_revision: int | None = Field(default=None, ge=0)
    current_item_id: str | None = None
    status: Literal["stopped", "playing", "paused"] = "stopped"
    order_mode: Literal["sequential", "shuffle", "radio"] = "sequential"
    repeat_mode: Literal["off", "all", "one"] = "off"
    progress_seconds: float = Field(default=0, ge=0)
    volume: float = Field(default=0.8, ge=0, le=1)
    history_item_ids: list[str] = Field(default_factory=list, max_length=5000)
    history_cursor: int = Field(default=-1, ge=-1)
    shuffle_bag_item_ids: list[str] = Field(default_factory=list, max_length=1000)
    items: list[PlaybackSessionItemRequest] = Field(default_factory=list, max_length=1000)


class PlayerActionAckRequest(BaseModel):
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=128)
    session_id: str = Field(default="default", min_length=1, max_length=128)
    status: Literal["succeeded", "failed"]
    result: dict[str, Any] = Field(default_factory=dict)


@router.get("")
async def read_playback_session(
    user_id: str = Query(DEFAULT_USER_ID, min_length=1, max_length=128),
):
    return get_playback_session(user_id)


@router.put("")
async def write_playback_session(req: PlaybackSessionRequest):
    try:
        return replace_playback_session(
            user_id=req.user_id,
            items=[item.model_dump(mode="json") for item in req.items],
            current_item_id=req.current_item_id,
            status=req.status,
            order_mode=req.order_mode,
            repeat_mode=req.repeat_mode,
            progress_seconds=req.progress_seconds,
            volume=req.volume,
            history_item_ids=req.history_item_ids,
            history_cursor=req.history_cursor,
            shuffle_bag_item_ids=req.shuffle_bag_item_ids,
            expected_revision=req.expected_revision,
        )
    except RevisionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "revision_conflict",
                "expected_revision": exc.expected,
                "actual_revision": exc.actual,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/actions/{action_id}/ack")
async def acknowledge_browser_action(action_id: str, req: PlayerActionAckRequest):
    from services.player_action_store import acknowledge_player_action

    action = acknowledge_player_action(
        action_id,
        user_id=req.user_id,
        session_id=req.session_id,
        status=req.status,
        result=req.result,
    )
    if action is None:
        raise HTTPException(status_code=404, detail="player action not found")
    return action
