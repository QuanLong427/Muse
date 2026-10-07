"""Explicit Bilibili egress policy, independent of the model provider's proxy."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from config import settings


def network_routes(mode: str | None = None, proxy_url: str | None = None) -> list[tuple[str, str]]:
    mode = mode if mode is not None else settings.BILIBILI_NETWORK_MODE
    if mode not in {"auto", "direct", "proxy"}:
        raise ValueError("BILIBILI_NETWORK_MODE must be auto, direct or proxy")
    proxy = proxy_url if proxy_url is not None else settings.BILIBILI_PROXY_URL
    if not proxy and mode != "direct":
        proxy = next((os.getenv(key, "") for key in (
            "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"
        ) if os.getenv(key)), "")
    if proxy and urlparse(proxy).scheme not in {"http", "https"}:
        raise ValueError("BILIBILI_PROXY_URL must use an http or https proxy")
    if mode == "proxy" and not proxy:
        raise ValueError("proxy mode requires BILIBILI_PROXY_URL")
    if mode == "direct":
        return [("direct", "")]
    if mode == "proxy":
        return [("proxy", proxy)]
    return [("direct", ""), *(([("proxy", proxy)]) if proxy else [])]


def redact_proxy(value: str) -> str:
    import re
    value = re.sub(r"(https?://)[^/\s@]+@", r"\1[redacted]@", value)
    for _, proxy in network_routes():
        if proxy:
            value = value.replace(proxy, "[configured proxy]")
            parsed = urlparse(proxy)
            if parsed.username:
                value = value.replace(parsed.username, "[redacted]")
            if parsed.password:
                value = value.replace(parsed.password, "[redacted]")
    return value
