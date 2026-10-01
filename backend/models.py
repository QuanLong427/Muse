from typing import Literal

from pydantic import BaseModel, Field


class Track(BaseModel):
    id: str
    title: str
    author: str
    date: str
    filename: str
    subDir: str
    size: int
    url: str
    bvid: str | None = None


class TrackCard(BaseModel):
    """Source-aware card sent to the client; it is not a playback-session item."""

    track_id: str
    source_type: Literal["local", "bilibili"]
    availability: Literal["local", "remote", "downloading", "failed"]
    title: str
    author: str = ""
    duration: str = ""
    bvid: str | None = None
    url: str = ""
    download_status: Literal["idle", "queued", "downloading", "downloaded", "failed"] = "idle"
    allowed_actions: list[Literal["play", "download", "add_to_session", "add_to_playlist"]] = Field(
        default_factory=list
    )
    local_track: Track | None = None


class ChatMessage(BaseModel):
    id: str
    role: str  # "agent" | "operator" | "system" | "tool"
    content: str
    timestamp: int
    toolName: str | None = None


class BiliVideo(BaseModel):
    bvid: str
    title: str
    author: str
    duration: str
    play: int
    pic: str


class DanmakuItem(BaseModel):
    time: float
    content: str
    type: int
    color: str
