import logging

from fastapi import APIRouter
from pydantic import BaseModel

from services.playlist_store import get_playlist, set_playlist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/playlist", tags=["playlist"])


class PlaylistRequest(BaseModel):
    tracks: list[dict]


@router.get("")
async def read_playlist():
    """Deprecated: return the active playback-session tracks."""
    return {"tracks": get_playlist(), "deprecated": True}


@router.post("")
async def write_playlist(req: PlaylistRequest):
    """Deprecated compatibility write for older clients."""
    set_playlist(req.tracks)
    return {"status": "ok", "count": len(req.tracks), "deprecated": True}
