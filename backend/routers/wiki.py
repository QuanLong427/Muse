import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import settings
from services.wiki_manager import audit_wiki_quality, get_wiki_status, init_wiki, reset_wiki
from services.wiki_ingest import ingest_song

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wiki", tags=["wiki"])


class IngestRequest(BaseModel):
    title: str
    artist: str = ""
    uploader: str = ""
    video_title: str = ""
    bvid: str = ""
    local_file_path: str = ""
    album: str = ""
    genre: str = ""
    description: str = ""
    duration: int = 0
    url: str = ""
    external_sources: list[dict[str, str]] = Field(default_factory=list)


@router.post("/init")
async def wiki_init():
    """Initialize the wiki directory structure."""
    result = init_wiki()
    return result


@router.get("/status")
async def wiki_status():
    """Get wiki initialization status and statistics."""
    return get_wiki_status()


@router.get("/audit")
async def wiki_audit():
    """Audit knowledge provenance and graph integrity without modifying data."""
    return await asyncio.to_thread(audit_wiki_quality, settings.WIKI_DIR)


@router.post("/reset")
async def wiki_reset():
    """Reset generated Wiki data without deleting local music files."""
    try:
        return await asyncio.to_thread(reset_wiki)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ingest")
async def wiki_ingest(req: IngestRequest):
    """Ingest a song into the wiki. Runs asynchronously."""
    wiki_dir = settings.WIKI_DIR

    # Check if wiki is initialized
    status = get_wiki_status(wiki_dir)
    if not status.get("initialized"):
        raise HTTPException(status_code=400, detail="Wiki not initialized. Call POST /api/wiki/init first.")

    song_meta = req.model_dump()

    # Run ingest in background to not block the response
    async def _run_ingest():
        try:
            await asyncio.to_thread(ingest_song, song_meta, wiki_dir)
            logger.info(f"Wiki ingest completed for: {req.title}")
        except Exception as e:
            logger.error(f"Wiki ingest failed for {req.title}: {e}")

    asyncio.create_task(_run_ingest())

    return {"status": "ingest_started", "title": req.title}
