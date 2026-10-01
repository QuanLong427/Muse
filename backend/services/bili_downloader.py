"""Reliable Bilibili audio download boundary built on yt-dlp.

The Agent never assembles shell commands.  URLs are validated, downloads run
sequentially with bounded retries, and optional browser cookies are read from a
Netscape-format file mounted into the backend container.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_BVID_PATTERN = re.compile(r"/video/(BV[0-9A-Za-z]{3,20})(?:[/?.]|$)")
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_BILIBILI_ORIGIN = "https://www.bilibili.com"


def extract_bvid(url: str) -> str:
    parsed = urlparse(url.strip())
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "bilibili.com" or hostname.endswith(".bilibili.com")
    ):
        raise ValueError("仅支持 https://*.bilibili.com/video/BV... 视频地址")
    match = _BVID_PATTERN.search(parsed.path + ("?" + parsed.query if parsed.query else ""))
    if not match:
        raise ValueError("B站视频地址缺少有效 BV 号")
    return match.group(1)


def _safe_filename_component(value: str, fallback: str) -> str:
    cleaned = _INVALID_FILENAME.sub("_", value).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or fallback)[:100]


def _cookie_file(path_value: str) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    try:
        if path.is_file() and path.stat().st_size > 0:
            return path
    except OSError:
        return None
    return None


def _existing_track(music_root: Path, bvid: str) -> Path | None:
    """Find an existing source across the whole library, not only today's folder."""
    matches = sorted(music_root.glob(f"**/*-{bvid}.mp3"))
    return matches[0] if matches else None


def _download_command(
    *,
    url: str,
    bvid: str,
    work_dir: Path,
    cookie_file: Path | None,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--newline",
        "--extract-audio",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "0",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--extractor-retries",
        "2",
        "--concurrent-fragments",
        "1",
        "--sleep-requests",
        "1",
        "--socket-timeout",
        "30",
        "--impersonate",
        "chrome",
        "--add-header",
        f"Referer:{_BILIBILI_ORIGIN}/",
        "--add-header",
        f"Origin:{_BILIBILI_ORIGIN}",
        "--output",
        str(work_dir / f"{bvid}.%(ext)s"),
    ]
    if cookie_file:
        command.extend(["--cookies", str(cookie_file)])
    command.append(url)
    return command


def _download_error(stderr: str, *, cookies_configured: bool) -> dict[str, Any]:
    lowered = stderr.lower()
    if "http error 412" in lowered or "request was banned" in lowered or "-412" in lowered:
        cookie_hint = (
            "如果直连后仍失败，当前 Cookie 可能已过期，请重新导出后再试。"
            if cookies_configured
            else "如果直连后仍失败，请导出 B 站浏览器 Cookie 到 secrets/bilibili-cookies.txt。"
        )
        return {
            "code": "bilibili_request_blocked",
            "message": (
                "B站拒绝了当前网络出口的下载请求（412 request was banned），"
                "这不表示视频本身不可访问。若正在使用 VPN，请关闭 VPN 或让 B 站域名直连。"
                f"{cookie_hint}"
            ),
            "retryable": False,
        }
    if "sign in" in lowered or "login" in lowered or "登录" in stderr:
        return {
            "code": "bilibili_login_required",
            "message": "该视频需要有效的 B 站登录 Cookie。",
            "retryable": False,
        }
    return {
        "code": "download_failed",
        "message": "B站音频下载或转换失败，请检查后端日志。",
        "retryable": True,
    }


def download_bilibili_audio(
    *,
    urls: list[str],
    metadata: list[dict[str, Any]],
    music_dir: str,
    cookie_file: str = "",
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Download Bilibili URLs sequentially and return stable local metadata."""
    root = Path(music_dir)
    if not root.is_dir():
        return {
            "success": False,
            "files": [],
            "errors": [
                {
                    "code": "music_dir_missing",
                    "message": f"MUSIC_DIR does not exist: {root}",
                    "retryable": False,
                }
            ],
        }
    if not urls:
        return {
            "success": False,
            "files": [],
            "errors": [
                {
                    "code": "missing_urls",
                    "message": "没有提供需要下载的 B 站视频地址。",
                    "retryable": False,
                }
            ],
        }

    china_time = timezone(timedelta(hours=8))
    target_dir = root / datetime.now(china_time).strftime("%Y%m%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    meta_by_bvid = {
        str(item.get("bvid")): item
        for item in metadata
        if isinstance(item, dict) and item.get("bvid")
    }
    cookies = _cookie_file(cookie_file)
    files: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for url in urls:
        try:
            bvid = extract_bvid(url)
        except ValueError as exc:
            errors.append(
                {
                    "code": "invalid_bilibili_url",
                    "message": str(exc),
                    "url": str(url)[:500],
                    "retryable": False,
                }
            )
            continue

        meta = meta_by_bvid.get(bvid, {})
        artist = _safe_filename_component(str(meta.get("artist", "")), "Unknown")
        video_title = str(meta.get("videoTitle") or meta.get("video_title") or "")
        title = _safe_filename_component(str(meta.get("title", "") or video_title), "Unknown")
        final_path = target_dir / f"{artist}-{title}-{bvid}.mp3"
        existing = _existing_track(root, bvid)
        if existing:
            final_path = existing
            files.append(
                _file_record(
                    original=existing.name,
                    final_path=final_path,
                    bvid=bvid,
                    title=title,
                    artist=artist,
                    uploader=str(meta.get("uploader", "")),
                    video_title=video_title,
                    url=url,
                    existing=True,
                )
            )
            continue

        try:
            with tempfile.TemporaryDirectory(prefix=".musicer-download-", dir=target_dir) as raw_work_dir:
                work_dir = Path(raw_work_dir)
                result = subprocess.run(
                    _download_command(
                        url=url,
                        bvid=bvid,
                        work_dir=work_dir,
                        cookie_file=cookies,
                    ),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=max(60, min(int(timeout_seconds), 900)),
                    check=False,
                )
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "yt-dlp exited with an error").strip()
                    error = _download_error(detail, cookies_configured=bool(cookies))
                    error.update({"bvid": bvid, "url": url, "detail": detail[-2000:]})
                    errors.append(error)
                    continue

                candidates = sorted(work_dir.glob("*.mp3"))
                if not candidates:
                    errors.append(
                        {
                            "code": "converted_file_missing",
                            "message": "yt-dlp 已结束，但没有生成 MP3 文件。",
                            "bvid": bvid,
                            "url": url,
                            "retryable": True,
                        }
                    )
                    continue
                downloaded = candidates[0]
                already_created = final_path.exists()
                if not already_created:
                    downloaded.replace(final_path)
                files.append(
                    _file_record(
                        original=downloaded.name,
                        final_path=final_path,
                        bvid=bvid,
                        title=title,
                        artist=artist,
                        uploader=str(meta.get("uploader", "")),
                        video_title=video_title,
                        url=url,
                        existing=already_created,
                    )
                )
        except subprocess.TimeoutExpired:
            errors.append(
                {
                    "code": "download_timeout",
                    "message": f"下载超过 {timeout_seconds} 秒，已终止。",
                    "bvid": bvid,
                    "url": url,
                    "retryable": True,
                }
            )
        except OSError as exc:
            errors.append(
                {
                    "code": "download_runtime_error",
                    "message": str(exc),
                    "bvid": bvid,
                    "url": url,
                    "retryable": False,
                }
            )

    return {
        "success": not errors and bool(files),
        "status": "success" if not errors and files else "partial" if files else "failed",
        "files": files,
        "errors": errors,
        "cookies_configured": bool(cookies),
    }


def _file_record(
    *,
    original: str,
    final_path: Path,
    bvid: str,
    title: str,
    artist: str,
    uploader: str,
    video_title: str,
    url: str,
    existing: bool,
) -> dict[str, Any]:
    return {
        "original": original,
        "renamed": final_path.name,
        "bvid": bvid,
        "title": "" if title == "Unknown" else title,
        "artist": "" if artist == "Unknown" else artist,
        "uploader": uploader,
        "video_title": video_title,
        "url": url,
        "local_file_path": str(final_path.resolve()),
        "existing": existing,
    }
