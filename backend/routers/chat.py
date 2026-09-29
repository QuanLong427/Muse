import json
import os
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import PROJECT_ROOT
from services.ai_agent import chat_stream
from services.memory_manager import reset_memory
from services.memory_store import (
    DEFAULT_USER_ID,
    clear_session,
    ensure_session,
)

router = APIRouter(tags=["chat"])


def _sse_response(result_text: str, *, session_id: str | None = None) -> StreamingResponse:
    """Build an SSE response with a single result event."""
    async def event():
        data = {'type': 'result', 'subtype': 'success', 'result': result_text}
        if session_id:
            data['session_id'] = session_id
        yield f"event: output\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        yield f"event: done\ndata: {json.dumps({'status': 'completed'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


class ChatRequest(BaseModel):
    message: str
    mode: str = "local"
    history: list[dict[str, str]] | None = None
    scenario: str = "默认"
    player_state: dict[str, Any] | None = None
    user_id: str = DEFAULT_USER_ID
    session_id: str | None = None


@router.post("/api/chat")
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    msg = req.message.strip()
    user_id = req.user_id.strip() or DEFAULT_USER_ID
    session_id = ensure_session(req.session_id, user_id, req.scenario)

    # Hardcoded slash commands — no LLM needed
    if msg == "/clear":
        clear_session(session_id, user_id)
        return _sse_response("当前会话屏幕已清空，长期记忆未删除", session_id=session_id)

    if msg == "/reset-wiki":
        wiki_dir = os.path.join(PROJECT_ROOT, "LLM-Wiki")
        try:
            if os.path.exists(wiki_dir):
                shutil.rmtree(wiki_dir)
            from services.wiki_manager import init_wiki
            init_wiki()
            return _sse_response("LLM-Wiki 已重置并重新初始化完成")
        except Exception as e:
            return _sse_response(f"重置失败: {e}")

    if msg == "/reset-memory":
        try:
            reset_memory(user_id)
            return _sse_response("当前用户记忆已重置完成", session_id=session_id)
        except Exception as e:
            return _sse_response(f"重置记忆失败: {e}")

    async def event_generator():
        async for event in chat_stream(
            message=msg,
            history=req.history or [],
            scenario=req.scenario,
            player_state=req.player_state,
            user_id=user_id,
            session_id=session_id,
        ):
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
