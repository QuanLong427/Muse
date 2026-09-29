from fastapi import APIRouter, Query
from services.memory_store import (
    DEFAULT_SESSION_ID,
    DEFAULT_USER_ID,
    ensure_session,
    get_session_history,
)

router = APIRouter(tags=["history"])


@router.get("/api/history")
async def get_history(
    user_id: str = Query(DEFAULT_USER_ID, max_length=128),
    session_id: str = Query(DEFAULT_SESSION_ID, max_length=160),
):
    """Return visible history for one isolated user session."""
    session_id = ensure_session(session_id, user_id, "默认")
    records = get_session_history(
        session_id,
        user_id,
        include_cleared=False,
        limit=500,
    )
    return {
        "history": records,
        "clear_offset": 0,
        "user_id": user_id,
        "session_id": session_id,
    }
