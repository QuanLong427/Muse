"""Chat draft protocol. Playlist selection and application live in services."""
import asyncio
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from routers.music_library import _translate_error
from services.playlist_draft_service import DraftConflictError, confirm_draft, edit_draft, get_draft
from fastapi import HTTPException

router = APIRouter(prefix="/api/playlist-drafts", tags=["playlist-drafts"])


class DraftConfirmation(BaseModel):
    user_id: str = Field(default="local", min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=256)
    expected_revision: int = Field(ge=0)


class DraftEdit(DraftConfirmation):
    action: Literal["rename", "remove", "reorder", "replace", "add", "filter_versions", "cancel"]
    name: str = Field(default="", max_length=100)
    item_ids: list[str] = Field(default_factory=list, max_length=50)
    track_id: str = Field(default="", max_length=1024)
    candidate_batch_id: str = Field(default="", max_length=128)
    candidate_track_id: str = Field(default="", max_length=1024)
    exclude_versions: list[str] = Field(default_factory=list, max_length=10)


def _error(exc):
    if isinstance(exc, DraftConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _translate_error(exc)


@router.get("/{draft_id}")
async def read(draft_id: str, user_id: str = Query("local", min_length=1, max_length=128), session_id: str | None = None):
    try:
        return await asyncio.to_thread(get_draft, draft_id, user_id, session_id)
    except Exception as exc:
        _error(exc)


@router.patch("/{draft_id}")
async def edit(draft_id: str, req: DraftEdit):
    try:
        return await asyncio.to_thread(edit_draft, draft_id, **req.model_dump())
    except Exception as exc:
        _error(exc)


@router.post("/{draft_id}/confirm")
async def confirm(draft_id: str, req: DraftConfirmation):
    try:
        return await asyncio.to_thread(confirm_draft, draft_id, **req.model_dump())
    except Exception as exc:
        _error(exc)
