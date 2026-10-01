"""Application-level operations used by the LLM-Wiki Skill CLI."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


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

    This is the shared application boundary. The Skill CLI is the adapter;
    ``services.wiki_ingest.ingest_song`` remains the domain writer.
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


_ENTITY_TYPES = {
    "songs": "song",
    "artists": "artist",
    "albums": "album",
    "genres": "genre",
}


def _read_frontmatter(content: str) -> dict[str, Any]:
    if not content.startswith("---\n"):
        return {}
    try:
        _, raw, _ = content.split("---", 2)
        parsed = yaml.safe_load(raw) or {}
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, yaml.YAMLError):
        return {}


def _read_section(content: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _query_terms(query: str, alias_index: dict[str, list[str]]) -> list[str]:
    terms = [query.strip()]
    folded_query = query.casefold()
    for canonical, aliases in alias_index.items():
        candidates = [canonical, *(aliases or [])]
        if any(candidate and candidate.casefold() in folded_query for candidate in candidates):
            terms.extend(candidate for candidate in candidates if candidate)
    # English identifiers and titles are useful even when the query is a full sentence.
    terms.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]+", query))
    return list(dict.fromkeys(term for term in terms if term.strip()))


def run_wiki_search(
    query: str,
    *,
    wiki_dir: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Return deterministic, source-preserving Wiki matches for the main Agent.

    This function deliberately does not call another LLM. It returns exact
    frontmatter, evidence, uncertainty and identifiers so the caller can
    summarize them without creating a competing Wiki query agent.
    """
    query = query.strip()
    if not query:
        raise ValueError("查询内容不能为空")
    if not 1 <= limit <= 20:
        raise ValueError("limit 必须在 1 到 20 之间")

    from config import settings
    from services.wiki_manager import get_wiki_status, load_alias_index

    root = Path(wiki_dir or settings.WIKI_DIR).resolve()
    status = get_wiki_status(str(root))
    if not status.get("initialized"):
        return {
            "status": "not_initialized",
            "query": query,
            "wiki_dir": str(root),
            "results": [],
        }

    alias_index = load_alias_index(str(root))
    terms = _query_terms(query, alias_index)
    folded_query = query.casefold()
    index_path = root / "index.md"
    index_content = index_path.read_text(encoding="utf-8", errors="replace") if index_path.is_file() else ""
    folded_index = index_content.casefold()
    matches: list[dict[str, Any]] = []

    entity_root = root / "wiki" / "entities"
    for directory, fallback_type in _ENTITY_TYPES.items():
        type_dir = entity_root / directory
        if not type_dir.is_dir():
            continue
        for page in type_dir.glob("*.md"):
            content = page.read_text(encoding="utf-8", errors="replace")
            folded_content = content.casefold()
            name = page.stem
            folded_name = name.casefold()
            score = 0
            matched_terms: list[str] = []
            if folded_name == folded_query:
                score = 150
                matched_terms.append(query)
            elif folded_name in folded_query:
                score = 120
                matched_terms.append(name)

            for term in terms:
                folded_term = term.casefold()
                if folded_term == folded_name:
                    score = max(score, 140)
                    matched_terms.append(term)
                elif folded_term in folded_name:
                    score = max(score, 90)
                    matched_terms.append(term)
                occurrences = folded_content.count(folded_term)
                if occurrences:
                    score += min(occurrences * 5, 50)
                    matched_terms.append(term)

            if f"[[{name}]]".casefold() in folded_index:
                score += 20
            if score <= 0:
                continue

            frontmatter = _read_frontmatter(content)
            schema_version = str(frontmatter.get("schema_version", ""))
            declared_status = str(frontmatter.get("verification_status", ""))
            effective_status = declared_status
            try:
                if float(schema_version) < 3.0:
                    effective_status = "needs_review"
            except (TypeError, ValueError):
                effective_status = "needs_review"
            if effective_status not in {"verified", "inferred", "needs_review"}:
                effective_status = "needs_review"

            sources = frontmatter.get("sources", [])
            if not isinstance(sources, list):
                sources = [sources] if sources else []
            exact_identifiers = list(
                dict.fromkeys(
                    [str(item) for item in sources]
                    + re.findall(r"\bBV[0-9A-Za-z]{8,}\b", content)
                )
            )
            matches.append(
                {
                    "name": name,
                    "entity_type": str(frontmatter.get("entity_type") or fallback_type),
                    "path": page.relative_to(root).as_posix(),
                    "score": score,
                    "schema_version": schema_version or None,
                    "verification_status": effective_status,
                    "declared_verification_status": declared_status or None,
                    "confidence": frontmatter.get("confidence"),
                    "sources": sources,
                    "exact_identifiers": exact_identifiers,
                    "overview": _read_section(content, "Overview"),
                    "evidence": _read_section(content, "Evidence"),
                    "uncertainties": _read_section(content, "Uncertainties"),
                    "links": list(dict.fromkeys(re.findall(r"\[\[([^\]]+)\]\]", content))),
                    "matched_terms": list(dict.fromkeys(matched_terms)),
                }
            )

    matches.sort(key=lambda item: (-item["score"], item["entity_type"], item["name"].casefold()))
    return {
        "status": "ok" if matches else "no_results",
        "query": query,
        "schema_version": status.get("schema_version"),
        "result_count": min(len(matches), limit),
        "results": matches[:limit],
        "answer_policy": {
            "verified": "可作为已核验事实",
            "inferred": "必须说明尚未独立核验",
            "needs_review": "只能作为待核验线索",
        },
    }
