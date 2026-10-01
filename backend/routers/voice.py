from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from services.voice_service import (
    MAX_AUDIO_BYTES,
    VoiceServiceError,
    synthesize_speech,
    transcribe_audio,
)


router = APIRouter(prefix="/api/voice", tags=["voice"])


async def _read_limited_audio(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="录音超过 10MB 限制")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/transcribe")
async def transcribe(request: Request):
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if not (media_type.startswith("audio/") or media_type == "application/octet-stream"):
        raise HTTPException(status_code=415, detail="请求体必须是音频数据")
    audio = await _read_limited_audio(request)
    try:
        text = await transcribe_audio(audio, media_type)
    except VoiceServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"text": text}


class SynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/synthesize")
async def synthesize(body: SynthesizeRequest):
    try:
        audio = await synthesize_speech(body.text)
    except VoiceServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(
        content=audio.content,
        media_type=audio.media_type,
        headers={"Cache-Control": "no-store"},
    )
