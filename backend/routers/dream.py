import asyncio

from fastapi import APIRouter
from fastapi import Query
from services.dream_engine import run_dream
from services.memory_store import DEFAULT_USER_ID

router = APIRouter(tags=["dream"])


@router.post("/api/dream")
async def trigger_dream(user_id: str = Query(DEFAULT_USER_ID, max_length=128)):
    """手动触发 Dream 引擎，从对话历史总结用户画像"""
    result = await asyncio.to_thread(run_dream, user_id)
    return result
