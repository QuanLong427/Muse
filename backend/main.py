from dotenv import load_dotenv
from pathlib import Path

# 加载 backend 目录下的 .env 和 .env.local 文件
_backend_dir = Path(__file__).resolve().parent
load_dotenv(_backend_dir / ".env", override=False)
load_dotenv(_backend_dir / ".env.local", override=True)

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from routers import bili, chat, config, dream, history, memory, music_library, playback, playlist, scenario, search, tracks, voice, wiki

logger = logging.getLogger(__name__)

# ── Dream Background Scheduler ───────────────────────────────────────────────

_dream_task = None

async def _dream_scheduler():
    """Background task: trigger Dream engine every DREAM_INTERVAL_HOURS."""
    interval = getattr(settings, "DREAM_INTERVAL_HOURS", 24) * 3600
    while True:
        await asyncio.sleep(interval)
        try:
            from services.dream_engine import run_dream
            from services.memory_store import list_users_with_pending_messages

            for user_id in list_users_with_pending_messages():
                result = await asyncio.to_thread(run_dream, user_id)
                logger.info(
                    f"[dream-scheduler] Auto Dream completed for {user_id}: {result}"
                )
        except Exception as e:
            logger.error(f"[dream-scheduler] Dream failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    global _dream_task

    # Startup: init system files
    try:
        from services.system_init import init_system_files
        init_system_files()
        from services.memory_manager import init_memory_system
        init_memory_system()
    except Exception as e:
        logger.error(f"[startup] System init failed: {e}")

    # Startup: initialize the playback session and migrate the legacy playlist table.
    try:
        from services.playback_session_store import init_playback_session_db
        init_playback_session_db()
        from services.music_library_store import init_music_library_db
        init_music_library_db()
        from services.player_action_store import init_player_action_db
        init_player_action_db()
    except Exception as e:
        logger.error(f"[startup] Playback session DB init failed: {e}")

    # Startup: start dream scheduler
    _dream_task = asyncio.create_task(_dream_scheduler())
    logger.info(f"[startup] Dream scheduler started (interval: {getattr(settings, 'DREAM_INTERVAL_HOURS', 24)}h)")

    yield

    # Shutdown
    if _dream_task:
        _dream_task.cancel()
        try:
            await _dream_task
        except asyncio.CancelledError:
            pass
    logger.info("[shutdown] Dream scheduler stopped")


app = FastAPI(title="Musicer Backend", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(bili.router)
app.include_router(chat.router)
app.include_router(config.router)
app.include_router(dream.router)
app.include_router(history.router)
app.include_router(memory.router)
app.include_router(music_library.router)
app.include_router(playback.router)
app.include_router(playlist.router)
app.include_router(scenario.router)
app.include_router(search.router)
app.include_router(tracks.router)
app.include_router(voice.router)
app.include_router(wiki.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
