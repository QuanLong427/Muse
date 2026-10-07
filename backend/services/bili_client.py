import hashlib
import re
import time
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from models import BiliVideo, DanmakuItem
from services.bili_network import network_routes, redact_proxy

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5,
    49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55,
    40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57,
    62, 11, 36, 20, 34, 44, 52,
]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

COMMON_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://www.bilibili.com",
    "Referer": "https://www.bilibili.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

KEY_TTL = 12 * 60 * 60  # 12 hours in seconds

_cached_keys: dict[str, Any] | None = None
_cached_buvid3: str | None = None


def _get_mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _sign_params(params: dict[str, Any], mixin_key: str) -> dict[str, str]:
    wts = int(time.time())
    signed = {k: str(v) for k, v in params.items()}
    signed["wts"] = str(wts)

    sorted_qs = "&".join(
        f"{quote(k, safe='')}={quote(v, safe='')}"
        for k, v in sorted(signed.items())
    )

    w_rid = hashlib.md5((sorted_qs + mixin_key).encode()).hexdigest()
    signed["w_rid"] = w_rid
    return signed


async def _ensure_buvid3(client: httpx.AsyncClient) -> str:
    global _cached_buvid3
    if _cached_buvid3:
        return _cached_buvid3

    try:
        res = await client.get(
            "https://www.bilibili.com",
            headers={"User-Agent": UA},
            follow_redirects=True,
        )
        for header_value in res.headers.get_list("set-cookie"):
            match = re.search(r"buvid3=([^;]+)", header_value)
            if match:
                _cached_buvid3 = match.group(1)
                return _cached_buvid3
    except Exception:
        pass

    _cached_buvid3 = f"{uuid.uuid4()}infoc"
    return _cached_buvid3


async def _get_wbi_keys(client: httpx.AsyncClient) -> tuple[str, str]:
    global _cached_keys
    if _cached_keys and (time.time() - _cached_keys["ts"]) < KEY_TTL:
        return _cached_keys["img_key"], _cached_keys["sub_key"]

    buvid3 = await _ensure_buvid3(client)
    headers = {**COMMON_HEADERS, "Cookie": f"buvid3={buvid3}"}
    res = await client.get(
        "https://api.bilibili.com/x/web-interface/nav", headers=headers
    )
    res.raise_for_status()
    json_data = res.json()
    wbi_img = json_data.get("data", {}).get("wbi_img", {})
    img_url = wbi_img.get("img_url", "")
    sub_url = wbi_img.get("sub_url", "")
    img_key = img_url.split("/")[-1].replace(".png", "") if img_url else ""
    sub_key = sub_url.split("/")[-1].replace(".png", "") if sub_url else ""

    if img_key and sub_key:
        _cached_keys = {"img_key": img_key, "sub_key": sub_key, "ts": time.time()}

    return img_key, sub_key


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]*>", "", s)


async def get_video_info(
    client: httpx.AsyncClient, bvid: str
) -> dict[str, str]:
    img_key, sub_key = await _get_wbi_keys(client)
    mixin_key = _get_mixin_key(img_key, sub_key)
    buvid3 = await _ensure_buvid3(client)

    params = _sign_params({"bvid": bvid}, mixin_key)
    qs = "&".join(
        f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in params.items()
    )
    headers = {**COMMON_HEADERS, "Cookie": f"buvid3={buvid3}"}
    res = await client.get(
        f"https://api.bilibili.com/x/web-interface/view?{qs}", headers=headers
    )
    json_data = res.json()

    if json_data.get("code") != 0 or not json_data.get("data", {}).get("cid"):
        raise Exception(f"Failed to get video info for {bvid}")

    return {
        "cid": str(json_data["data"]["cid"]),
        "title": json_data["data"].get("title", ""),
    }


async def get_danmaku(
    client: httpx.AsyncClient, cid: str
) -> list[DanmakuItem]:
    buvid3 = await _ensure_buvid3(client)
    headers = {**COMMON_HEADERS, "Cookie": f"buvid3={buvid3}"}
    res = await client.get(
        f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}", headers=headers
    )
    xml = res.text

    items: list[DanmakuItem] = []
    for match in re.finditer(r'<d p="([^"]*)"[^>]*>([^<]*)</d>', xml):
        attrs = match.group(1).split(",")
        time_val = float(attrs[0]) if attrs[0] else 0.0
        type_val = int(attrs[1]) if attrs[1] else 0
        color_int = int(attrs[3]) if len(attrs) > 3 and attrs[3] else 0xFFFFFF
        color = f"#{color_int:06x}"
        content = match.group(2)
        if content.strip():
            items.append(DanmakuItem(time=time_val, content=content, type=type_val, color=color))

    items.sort(key=lambda x: x.time)
    return items


async def search_videos(
    client: httpx.AsyncClient, keyword: str, page: int = 1
) -> dict[str, Any]:
    img_key, sub_key = await _get_wbi_keys(client)
    mixin_key = _get_mixin_key(img_key, sub_key)
    buvid3 = await _ensure_buvid3(client)

    params = _sign_params(
        {"search_type": "video", "keyword": keyword, "page": page, "order": "totalrank"},
        mixin_key,
    )
    qs = "&".join(
        f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in params.items()
    )
    headers = {**COMMON_HEADERS, "Cookie": f"buvid3={buvid3}"}
    res = await client.get(
        f"https://api.bilibili.com/x/web-interface/search/type?{qs}",
        headers=headers,
    )
    res.raise_for_status()
    # Try multiple encodings to handle Bilibili API responses
    import json
    content = res.content
    # Try UTF-8 first, then GBK, then Latin-1 as fallback
    for encoding in ["utf-8", "gbk", "gb2312", "latin-1"]:
        try:
            json_data = json.loads(content.decode(encoding))
            break
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    else:
        # If all encodings fail, use UTF-8 with replacement characters
        json_data = json.loads(content.decode("utf-8", errors="replace"))

    if json_data.get("code") != 0:
        raise RuntimeError(f"B站搜索失败，code={json_data.get('code')}")
    if not json_data.get("data", {}).get("result"):
        return {"total": 0, "videos": []}

    videos = []
    for v in json_data["data"]["result"]:
        bvid = v.get("bvid")
        if not bvid:
            continue
        pic = v.get("pic", "")
        if pic and pic.startswith("//"):
            pic = f"https:{pic}"
        videos.append(
            BiliVideo(
                bvid=bvid,
                title=_strip_html(v.get("title", "")),
                author=v.get("author", ""),
                duration=v.get("duration", ""),
                play=v.get("play", 0),
                pic=pic,
            )
        )

    total = json_data["data"].get("numResults", len(videos))
    return {"total": total, "videos": videos}


async def search_with_network_policy(keyword: str, page: int = 1) -> dict[str, Any]:
    """Try each distinct egress once; never hide a rejected search as no results."""
    attempts: list[str] = []
    detail = "B站搜索失败"
    for route, proxy in network_routes():
        attempts.append(route)
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False, proxy=proxy or None) as client:
                result = await search_videos(client, keyword) if page == 1 else await search_videos(client, keyword, page)
            return {"status": "ok", **result, "videos": [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in result.get("videos", [])
            ], "network_route": route, "network_attempts": attempts}
        except Exception as exc:
            detail = redact_proxy(str(exc))
            transient = isinstance(exc, (httpx.TimeoutException, httpx.TransportError))
            if transient and len(attempts) == len(network_routes()):
                return {"status": "error", "error_code": "bilibili_transient_error", "error": detail,
                        "retryable": True, "attempts": len(attempts), "network_attempts": attempts,
                        "total": 0, "videos": []}
    return {"status": "error", "error_code": "bilibili_search_failed", "error": detail,
            "retryable": False, "attempts": len(attempts), "network_attempts": attempts,
            "total": 0, "videos": []}


def search_cloud_candidates(keyword: str) -> dict[str, Any]:
    import asyncio
    return asyncio.run(search_with_network_policy(keyword))
