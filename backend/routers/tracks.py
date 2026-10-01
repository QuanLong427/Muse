import os
import re
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from services.music_manager import find_track_by_bvid, find_track_by_id, resolve_music_path, scan_subdir
from config import settings

router = APIRouter(tags=["tracks"])


@router.get("/api/tracks/by-bvid")
async def get_track_by_bvid(bvid: str = Query(..., min_length=1)):
    track = find_track_by_bvid(bvid)
    if not track:
        raise HTTPException(status_code=404, detail="not found")
    return track


@router.get("/api/tracks/by-id")
async def get_track_by_id(track_id: str = Query(..., min_length=1)):
    """Resolve a canonical local Track without treating a path-like id as remote."""
    track = find_track_by_id(track_id)
    if not track:
        raise HTTPException(status_code=404, detail="not found")
    return track


@router.get("/api/tracks/scan")
async def tracks_scan(subDir: str = Query(..., min_length=1)):
    tracks = scan_subdir(subDir)
    return {"tracks": tracks}


@router.get("/api/tracks/{path:path}")
async def serve_track(request: Request, path: str):
    relative_path = unquote(path)
    full_path = resolve_music_path(relative_path)

    if not full_path:
        raise HTTPException(status_code=403, detail="forbidden")

    file_size = os.path.getsize(full_path)
    range_header = request.headers.get("range")

    if range_header:
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            end = min(end, file_size - 1)
            chunk_size = end - start + 1

            def iter_file():
                with open(full_path, "rb") as f:
                    f.seek(start)
                    remaining = chunk_size
                    while remaining > 0:
                        read_size = min(remaining, 65536)
                        data = f.read(read_size)
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            return Response(
                content=b"".join(iter_file()),
                status_code=206,
                headers={
                    "Content-Type": "audio/mpeg",
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Content-Length": str(chunk_size),
                    "Accept-Ranges": "bytes",
                },
            )

    return FileResponse(
        full_path,
        media_type="audio/mpeg",
        headers={
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
        },
    )
