"""HTTP boundary for persistent background download jobs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from services.download_job_service import (
    DEFAULT_USER_ID,
    create_download_job,
    get_download_job,
    list_download_jobs,
    request_download_job_cancel,
    retry_download_job,
)


router = APIRouter(prefix="/api/download-jobs", tags=["downloads"])


class DownloadJobItemRequest(BaseModel):
    bvid: str = Field(min_length=4, max_length=32)
    url: str = Field(default="", max_length=1000)
    title: str = Field(default="", max_length=300)
    artist: str = Field(default="", max_length=300)
    uploader: str = Field(default="", max_length=300)
    video_title: str = Field(default="", max_length=1000)


class CreateDownloadJobRequest(BaseModel):
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=128)
    items: list[DownloadJobItemRequest] = Field(min_length=1, max_length=20)


class DownloadJobActionRequest(BaseModel):
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=128)


@router.get("")
async def read_download_jobs(
    user_id: str = Query(DEFAULT_USER_ID, min_length=1, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    active_only: bool = Query(default=False),
):
    return {
        "jobs": list_download_jobs(
            user_id=user_id,
            limit=limit,
            active_only=active_only,
        )
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def write_download_job(req: CreateDownloadJobRequest):
    try:
        job = create_download_job(
            user_id=req.user_id,
            items=[item.model_dump(mode="json") for item in req.items],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "queued", "job": job}


@router.get("/{job_id}")
async def read_download_job(
    job_id: str,
    user_id: str = Query(DEFAULT_USER_ID, min_length=1, max_length=128),
):
    job = get_download_job(job_id, user_id=user_id)
    if job is None:
        raise HTTPException(status_code=404, detail="download job not found")
    return job


@router.post("/{job_id}/cancel")
async def cancel_download_job(job_id: str, req: DownloadJobActionRequest):
    job = request_download_job_cancel(job_id, user_id=req.user_id)
    if job is None:
        raise HTTPException(status_code=404, detail="download job not found")
    return job


@router.post("/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_failed_download_job(job_id: str, req: DownloadJobActionRequest):
    try:
        job = retry_download_job(job_id, user_id=req.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="download job not found")
    return {"status": "queued", "job": job}


@router.post("/{job_id}/wiki-retry")
async def retry_knowledge_build(job_id: str, req: DownloadJobActionRequest):
    job = get_download_job(job_id, user_id=req.user_id)
    if job is None:
        raise HTTPException(status_code=404, detail="download job not found")
    from services.wiki_sync import retry_wiki_enrichment
    restarted = []
    for source in job.get("result", {}).get("wiki_sync", {}).get("jobs", []):
        if source.get("enrichment_status") in {"failed", "needs_review"}:
            restarted.append(retry_wiki_enrichment(source["id"]))
    return {"jobs": restarted}
