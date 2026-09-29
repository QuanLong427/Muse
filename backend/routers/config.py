from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings

router = APIRouter(tags=["config"])


def _mask_key(key: str) -> str:
    if len(key) <= 4:
        return "****"
    return key[:4] + "****"


def _is_masked_key(key: str) -> bool:
    """Prevent the settings form from persisting its masked placeholder."""
    return key.endswith("****")


@router.get("/api/config")
async def get_config():
    return {
        "base_url": settings.OPENAI_BASE_URL,
        "api_key": _mask_key(settings.OPENAI_API_KEY),
        "model_name": settings.MODEL_NAME,
    }


class ConfigUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model_name: str | None = None


@router.put("/api/config")
async def update_config(body: ConfigUpdate):
    updates: dict[str, str] = {}
    if body.base_url is not None:
        updates["OPENAI_BASE_URL"] = body.base_url.strip().rstrip("/")
    if body.api_key is not None and not _is_masked_key(body.api_key):
        updates["OPENAI_API_KEY"] = body.api_key.strip()
    if body.model_name is not None:
        updates["MODEL_NAME"] = body.model_name.strip()

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    settings.update(**updates)
    return {"ok": True}
