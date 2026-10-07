from fastapi import APIRouter, HTTPException, Query
import httpx

from services import bili_client

router = APIRouter(prefix="/api/bili", tags=["bili"])


@router.get("/search")
async def bili_search(
    keyword: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
):
    try:
        result = await bili_client.search_with_network_policy(keyword, page)
        if result.get("status") == "error":
            raise HTTPException(status_code=502, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/danmaku")
async def bili_danmaku(bvid: str = Query(..., min_length=1)):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            video_info = await bili_client.get_video_info(client, bvid)
            cid = video_info["cid"]
            danmaku = await bili_client.get_danmaku(client, cid)
        return {"bvid": bvid, "cid": cid, "danmaku": danmaku}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
