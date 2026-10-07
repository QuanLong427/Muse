"""Inspectable management API for the Musicer memory system."""

import asyncio
from fastapi import APIRouter, Query

from services.memory_manager import pending_history_count, sync_profile_projection
from services.memory_store import (
    DEFAULT_USER_ID,
    forget_memory_item,
    list_memory_episodes,
    list_memory_items,
    search_memory_episodes,
    search_messages,
)


router = APIRouter(tags=["memory"])


@router.get("/api/memory/tasks")
async def get_tasks(user_id: str = Query(DEFAULT_USER_ID, min_length=1, max_length=128),
                    session_id: str = Query("default", min_length=1, max_length=256)):
    from services.agent_context import live_task_context
    return await asyncio.to_thread(live_task_context, user_id, session_id)


@router.get("/api/memory/profile")
async def get_profile(user_id: str = Query(DEFAULT_USER_ID, min_length=1, max_length=128),
                      scenario: str = Query("默认", min_length=1, max_length=80)):
    from services.memory_profile_service import get_scene_profile
    return await asyncio.to_thread(get_scene_profile, user_id, scenario)


@router.get("/api/memory")
async def get_memory(
    user_id: str = Query(DEFAULT_USER_ID, max_length=128),
    scenario: str | None = Query(None, max_length=80),
):
    """Inspect active durable memories without exposing raw database state."""
    return {
        "user_id": user_id,
        "items": list_memory_items(user_id, scenario=scenario, limit=200),
        "pending_messages": pending_history_count(user_id),
    }


@router.get("/api/memory/search")
async def search_memory(
    q: str = Query(..., min_length=1, max_length=300),
    user_id: str = Query(DEFAULT_USER_ID, max_length=128),
    scenario: str | None = Query(None, max_length=80),
    limit: int = Query(8, ge=1, le=20),
):
    """Search attributable source messages from the episodic store."""
    return {
        "query": q,
        "messages": search_messages(
            q,
            user_id=user_id,
            scenario=scenario,
            limit=limit,
        ),
    }


@router.get("/api/memory/episodes")
async def get_memory_episodes(
    q: str | None = Query(None, min_length=1, max_length=300),
    user_id: str = Query(DEFAULT_USER_ID, max_length=128),
    scenario: str | None = Query(None, max_length=80),
    episode_type: str | None = Query(None, max_length=80),
    limit: int = Query(20, ge=1, le=100),
):
    """Inspect or search structured, attributable turn episodes."""
    if q:
        episodes = search_memory_episodes(
            q,
            user_id=user_id,
            scenario=scenario,
            limit=min(limit, 10),
        )
    else:
        episodes = list_memory_episodes(
            user_id,
            scenario=scenario,
            episode_type=episode_type,
            limit=limit,
        )
    return {"query": q, "user_id": user_id, "episodes": episodes}


@router.delete("/api/memory/items/{memory_key}")
async def forget_memory(
    memory_key: str,
    user_id: str = Query(DEFAULT_USER_ID, max_length=128),
    scenario: str | None = Query(None, max_length=80),
):
    """Soft-delete a durable memory and refresh the Markdown projection."""
    count = forget_memory_item(
        user_id=user_id,
        memory_key=memory_key,
        scenario=scenario,
    )
    sync_profile_projection(user_id)
    return {"status": "forgotten", "count": count, "memory_key": memory_key}
