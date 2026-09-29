"""Application-level operations shared by Agent tools and the LLM-Wiki CLI."""

from __future__ import annotations

from typing import Any


IDENTITY_FIELDS = (
    "title",
    "video_title",
    "videoTitle",
    "bvid",
    "url",
    "local_file_path",
)


def _normalize_ingest_items(payload: Any) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, list) else [payload]
    if not items or any(not isinstance(item, dict) for item in items):
        raise ValueError("元数据必须是 JSON 对象或非空对象数组")

    normalized = []
    for index, item in enumerate(items):
        if not any(item.get(field) for field in IDENTITY_FIELDS):
            raise ValueError(f"第 {index + 1} 条元数据缺少来源标识字段")
        metadata = dict(item)
        if metadata.get("videoTitle") and not metadata.get("video_title"):
            metadata["video_title"] = metadata["videoTitle"]
        from services.web_tools import validate_cached_external_sources

        external_sources, source_issues = validate_cached_external_sources(
            metadata.get("external_sources")
        )
        metadata["external_sources"] = external_sources
        if source_issues:
            metadata["external_source_issues"] = source_issues
        normalized.append(metadata)
    return normalized


def run_wiki_ingest(
    payload: Any,
    *,
    apply: bool = False,
    wiki_dir: str | None = None,
) -> dict[str, Any]:
    """Validate/preview an ingest request or explicitly apply it.

    This is the shared application boundary. The Skill CLI and Agent tool are
    adapters; ``services.wiki_ingest.ingest_song`` remains the domain writer.
    """
    normalized = _normalize_ingest_items(payload)
    preview = [
        {
                key: item.get(key, "")
                for key in ("bvid", "url", "title", "video_title", "uploader", "local_file_path")
            } | {
                "external_source_count": len(item.get("external_sources", [])),
                "external_source_issues": item.get("external_source_issues", []),
            }
            for item in normalized
        ]

    if not apply:
        return {
            "status": "planned",
            "count": len(normalized),
            "sources": preview,
            "writes_performed": False,
            "apply_requires_explicit_user_intent": True,
        }

    from services.wiki_ingest import ingest_song
    from services.wiki_manager import get_wiki_status, init_wiki

    if not get_wiki_status(wiki_dir).get("initialized"):
        init_wiki(wiki_dir)

    results = []
    errors = []
    for index, metadata in enumerate(normalized, start=1):
        try:
            results.append(ingest_song(metadata, wiki_dir))
        except Exception as exc:
            errors.append({"index": index, "error": str(exc)})

    status = "completed" if not errors else ("failed" if not results else "partial_failure")
    return {
        "status": status,
        "count": len(normalized),
        "results": results,
        "errors": errors,
        "writes_performed": bool(results),
    }
