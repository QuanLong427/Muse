import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import settings
from services import web_tools
from services.ai_agent import _build_tools
from services.web_tools import (
    _extract_page_text,
    _native_qwen_search_url,
    _validate_public_url,
    fetch_web_page,
    qwen_web_search,
    validate_cached_external_sources,
)
from services.wiki_ingest import _save_raw_material


def test_native_qwen_search_url_preserves_configured_host():
    assert _native_qwen_search_url(
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    ) == (
        "https://workspace.cn-beijing.maas.aliyuncs.com/"
        "api/v1/services/aigc/multimodal-generation/generation"
    )


def test_qwen_search_returns_attributable_results(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MODEL_NAME", "qwen3.5-flash")
    monkeypatch.setattr(web_tools, "WEB_CACHE_DIR", tmp_path)
    captured = {}
    request_count = 0

    def fake_request(payload):
        nonlocal request_count
        request_count += 1
        captured.update(payload)
        return {
            "output": {
                "search_info": {
                    "search_results": [
                        {
                            "index": 1,
                            "title": "翻唱说明",
                            "url": "https://example.com/cover",
                            "site_name": "Example",
                        }
                    ]
                },
                "choices": [{"message": {"content": [{"text": "这是翻唱候选。"}]}}],
            }
        }

    monkeypatch.setattr(web_tools, "_qwen_search_request", fake_request)
    result = qwen_web_search("测试歌曲 翻唱者", strategy="agent")

    assert result["status"] == "ok"
    assert result["results"][0]["url"] == "https://example.com/cover"
    assert result["summary"] == "这是翻唱候选。"
    options = captured["parameters"]["search_options"]
    assert options["forced_search"] is True
    assert options["enable_source"] is True
    assert options["search_strategy"] == "agent"

    cached = qwen_web_search("测试歌曲 翻唱者", strategy="agent")
    assert cached["cached"] is True
    assert request_count == 1


def test_html_extraction_removes_scripts_and_keeps_description():
    title, description, content = _extract_page_text(
        """
        <html><head><title>翻唱页面</title>
        <meta charset="utf-8">
        <meta name="description" content="演唱者说明">
        <script>ignore this instruction</script></head>
        <body><h1>歌曲</h1><p>本版本由小明演唱。</p></body></html>
        """,
        "text/html",
    )

    assert title == "翻唱页面"
    assert description == "演唱者说明"
    assert content == "歌曲 本版本由小明演唱。"
    assert "ignore this instruction" not in content


def test_fetch_cache_allows_only_grounded_external_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr(web_tools, "WEB_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        web_tools,
        "_download_public_page",
        lambda url: (
            url,
            "text/html",
            "<title>来源页</title><p>本版本由小明翻唱。</p>".encode("utf-8"),
            "utf-8",
        ),
    )

    fetched = fetch_web_page("https://example.com/source")
    accepted, issues = validate_cached_external_sources([{
        "url": fetched["url"],
        "title": fetched["title"],
        "source_type": "video_description",
        "content_hash": fetched["content_hash"],
        "quote": "本版本由小明翻唱。",
    }])

    assert not issues
    assert accepted[0]["quote"] == "本版本由小明翻唱。"

    rejected, issues = validate_cached_external_sources([{
        "url": fetched["url"],
        "content_hash": fetched["content_hash"],
        "quote": "网页中不存在的说法",
    }])
    assert rejected == []
    assert "引用不在抓取正文中" in issues[0]


def test_raw_song_record_embeds_only_validated_web_quote(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    wiki_dir = tmp_path / "wiki"
    monkeypatch.setattr(web_tools, "WEB_CACHE_DIR", cache_dir)
    monkeypatch.setattr(
        web_tools,
        "_download_public_page",
        lambda url: (
            url,
            "text/html",
            "<title>来源页</title><p>当前视频由小明演唱。</p>".encode("utf-8"),
            "utf-8",
        ),
    )
    fetched = fetch_web_page("https://example.com/source")

    raw_path = _save_raw_material({
        "bvid": "BV1WEB",
        "title": "测试歌曲",
        "external_sources": [{
            "url": fetched["url"],
            "content_hash": fetched["content_hash"],
            "quote": "当前视频由小明演唱。",
        }],
    }, str(wiki_dir))
    content = open(raw_path, encoding="utf-8").read()

    assert "external_source_count: 1" in content
    assert "当前视频由小明演唱。" in content
    assert "https://example.com/source" in content


def test_private_network_targets_are_blocked(monkeypatch):
    monkeypatch.setattr(
        web_tools.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(ValueError, match="内网"):
        _validate_public_url("http://example.test/private")


def test_agent_registers_explicit_web_tools():
    tools = {tool.name: tool for tool in _build_tools()}
    assert "web_search" in tools
    assert "web_fetch" in tools
