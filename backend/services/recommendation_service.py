"""Deterministic, auditable local recommendation services.

Radio may extend the active PlaybackSession, but it never downloads remote
media, changes named playlists, or writes long-term memories. Ranking consumes
the disposable recent-preference projection and persists every non-empty batch
so explanations can cite the exact decision evidence later.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from models import Track
from services.music_library_store import (
    list_feedback_excluded_track_ids,
    list_recent_tracks,
    record_recommendation_batch,
)
from services.music_manager import scan_tracks
from services.memory_store import list_memory_items
from services.preference_service import (
    build_recent_preference_profile,
    get_preference_window,
)


SourcePolicy = Literal["balanced", "local", "cloud"]
CloudSearch = Callable[[str], dict[str, Any]]
_COMPILATION_MARKERS = (
    "合集",
    "全集",
    "专辑",
    "串烧",
    "歌单",
    "盘点",
    "小时",
    "首歌曲",
)
_UNREQUESTED_VERSION_MARKERS = (
    "翻唱",
    "伴奏",
    "纯音乐",
    "remix",
    "cover",
    "live",
    "现场",
    "dj版",
    "作曲",
    "创作",
    "写给",
    "致敬",
    "模仿",
    "风格",
    "试听",
    "又出新歌",
    "最新歌曲",
)


def _fold(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", str(value or "").casefold())


def _source_targets(count: int, source_policy: SourcePolicy) -> dict[str, int]:
    if source_policy == "local":
        return {"local": count, "cloud": 0}
    if source_policy == "cloud":
        return {"local": 0, "cloud": count}
    local = math.ceil(count / 2)
    return {"local": local, "cloud": count - local}


def _duration_seconds(value: Any) -> int | None:
    parts = str(value or "").strip().split(":")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _song_identity_from_title(
    value: Any,
    known_songs: list[str] | None = None,
) -> str:
    """Return a conservative song identity for cloud-result de-duplication."""
    title = str(value or "").strip()
    folded_title = _fold(title)
    for song in known_songs or []:
        folded_song = _fold(song)
        if folded_song and folded_song in folded_title:
            return folded_song
    song_title = re.search(r"《([^》]{1,80})》", title)
    if song_title:
        return _fold(song_title.group(1))
    bracketed = re.search(r"【([^】]{1,80})】", title)
    return _fold(bracketed.group(1) if bracketed else title)


def _usable_cloud_video(video: dict[str, Any], artist: str = "") -> bool:
    title = str(video.get("title") or "").strip()
    bvid = str(video.get("bvid") or "").strip()
    if not title or not bvid or any(marker in title for marker in _COMPILATION_MARKERS):
        return False
    folded_title = title.casefold()
    if any(marker in folded_title for marker in _UNREQUESTED_VERSION_MARKERS):
        return False
    if re.search(r"(?<![a-z])ai(?![a-z])", folded_title):
        return False
    duration = _duration_seconds(video.get("duration"))
    if duration is not None and not 60 <= duration <= 12 * 60:
        return False
    return not artist or _fold(artist) in _fold(title)


def _memory_preference_seeds(user_id: str, scenario: str) -> dict[str, list[str]]:
    seeds = {"artists": [], "genres": [], "songs": []}
    for item in list_memory_items(
        user_id,
        scenario=scenario or "默认",
        include_global=True,
        limit=30,
    ):
        key = str(item.get("memory_key") or "").strip()
        prefix, separator, value = key.partition(":")
        if not separator or not value.strip():
            continue
        target = {
            "artist": "artists",
            "performer": "artists",
            "genre": "genres",
            "song": "songs",
            "track": "songs",
        }.get(prefix.casefold())
        if target and value.strip() not in seeds[target]:
            seeds[target].append(value.strip())
    return seeds


def _wiki_recommendation_context(seed_terms: list[str]) -> dict[str, Any]:
    """Expand verified/inferred Wiki entities one hop without another LLM."""
    from services.wiki_operations import run_wiki_search

    context: dict[str, Any] = {
        "status": "no_results",
        "artists": [],
        "genres": [],
        "songs": [],
        "evidence": [],
    }
    pending = [term for term in seed_terms if term][:8]
    visited: set[str] = set()
    try:
        while pending and len(visited) < 24:
            term = pending.pop(0)
            folded = _fold(term)
            if not folded or folded in visited:
                continue
            visited.add(folded)
            result = run_wiki_search(term, limit=8)
            for item in result.get("results", []):
                if not isinstance(item, dict):
                    continue
                verification = str(item.get("verification_status") or "needs_review")
                if verification not in {"verified", "inferred"}:
                    continue
                name = str(item.get("name") or "").strip()
                entity_type = str(item.get("entity_type") or "")
                bucket = {
                    "artist": "artists",
                    "genre": "genres",
                    "song": "songs",
                }.get(entity_type)
                if bucket and name and name not in context[bucket]:
                    context[bucket].append(name)
                context["evidence"].append(
                    {
                        "name": name,
                        "entity_type": entity_type,
                        "verification_status": verification,
                        "path": item.get("path"),
                    }
                )
                for link in item.get("links") or []:
                    link_text = str(link).strip()
                    if link_text and _fold(link_text) not in visited and len(pending) < 16:
                        pending.append(link_text)
        if context["evidence"]:
            context["status"] = "ok"
    except Exception as exc:
        context.update({"status": "error", "error": str(exc)})
    return context


def _candidate_matches_scope(
    track: Track,
    *,
    artist: str,
    genre: str,
    wiki: dict[str, Any],
) -> bool:
    searchable = _fold(f"{track.title} {track.author} {track.filename}")
    if artist:
        artist_match = _fold(artist) in searchable
        wiki_song_match = any(_fold(song) in searchable for song in wiki.get("songs", []))
        return artist_match or wiki_song_match
    if genre:
        related_artists = wiki.get("artists", [])
        related_songs = wiki.get("songs", [])
        return any(_fold(value) in searchable for value in [*related_artists, *related_songs])
    return True


def _cloud_queries(
    *,
    artist: str,
    genre: str,
    wiki: dict[str, Any],
    recent_profile: dict[str, Any],
    memory_seeds: dict[str, list[str]],
    seed_songs: list[str],
    catalog_artists: list[str],
    limit: int,
) -> list[str]:
    queries: list[str] = []
    wiki_songs = [str(value) for value in wiki.get("songs", []) if value]
    if artist:
        queries.extend(f"{artist} {song}" for song in seed_songs[:limit])
        queries.extend(f"{artist} {song}" for song in wiki_songs[:limit])
        queries.append(artist)
    elif genre:
        queries.extend(seed_songs[:limit])
        queries.extend(str(value) for value in wiki.get("artists", [])[:limit])
        queries.append(genre)
    else:
        queries.extend(seed_songs[:limit])
        queries.extend(memory_seeds["songs"][:limit])
        queries.extend(memory_seeds["artists"][:limit])
        queries.extend(
            str(item.get("author") or "")
            for item in recent_profile.get("artists", [])[:limit]
            if str(item.get("author") or "").strip()
        )
        queries.extend(wiki_songs[:limit])
        queries.extend(catalog_artists[:limit])
    normalized: list[str] = []
    seen: set[str] = set()
    for query in queries:
        query = query.strip()
        key = _fold(query)
        if query and key not in seen:
            normalized.append(query)
            seen.add(key)
    return normalized[: max(2, min(limit * 2, 8))]


def recommend_conversational_tracks(
    *,
    user_id: str,
    scenario: str = "默认",
    count: int = 4,
    source_policy: SourcePolicy = "balanced",
    query: str = "",
    artist: str = "",
    genre: str = "",
    seed_songs: list[str] | None = None,
    cloud_search: CloudSearch | None = None,
) -> dict[str, Any]:
    """Create an auditable local/cloud recommendation batch.

    The function owns source quotas, dynamic reallocation, Wiki expansion and
    deterministic ranking.  It never downloads media or changes playback.
    """
    requested_count = max(1, min(int(count), 12))
    if source_policy not in {"balanced", "local", "cloud"}:
        raise ValueError("unsupported source_policy")
    artist = artist.strip()
    genre = genre.strip()
    normalized_seed_songs: list[str] = []
    seen_seed_songs: set[str] = set()
    for song in seed_songs or []:
        song = str(song).strip()
        key = _fold(song)
        if song and key and key not in seen_seed_songs:
            normalized_seed_songs.append(song)
            seen_seed_songs.add(key)
        if len(normalized_seed_songs) >= 12:
            break
    scenario = scenario.strip() or "默认"
    planned = _source_targets(requested_count, source_policy)
    catalog = scan_tracks()
    profile = build_recent_preference_profile(
        user_id,
        catalog_tracks=catalog,
        scenario=scenario,
    )
    recent_profile = get_preference_window(profile, 7)
    memory_seeds = _memory_preference_seeds(user_id, scenario)
    seed_terms = [
        artist,
        genre,
        *normalized_seed_songs,
        *memory_seeds["artists"],
        *memory_seeds["genres"],
    ]
    seed_terms.extend(
        str(item.get("author") or "")
        for item in recent_profile.get("artists", [])[:5]
    )
    wiki = _wiki_recommendation_context(list(dict.fromkeys(filter(None, seed_terms))))

    feedback_excluded_ids = list_feedback_excluded_track_ids(user_id)
    recent_ids = {
        str(entry.get("track", {}).get("id") or "")
        for entry in list_recent_tracks(user_id=user_id, limit=30)
    }
    track_scores, max_track_score = _positive_score_map(
        recent_profile.get("tracks"), "track_id"
    )
    artist_scores, max_artist_score = _positive_score_map(
        recent_profile.get("artists"), "author"
    )
    date_key = datetime.now(timezone.utc).date().isoformat()
    local_ranked: list[dict[str, Any]] = []
    for track in catalog:
        if track.id in feedback_excluded_ids or not _candidate_matches_scope(
            track, artist=artist, genre=genre, wiki=wiki
        ):
            continue
        item = _rank_candidate(
            track,
            user_id=user_id,
            date_key=date_key,
            track_scores=track_scores,
            max_track_score=max_track_score,
            artist_scores=artist_scores,
            max_artist_score=max_artist_score,
            current_author="",
        )
        if artist and _fold(artist) in _fold(f"{track.author} {track.title}"):
            item["score"] = round(float(item["score"]) + 1.0, 6)
            item["reasons"].append(
                {"code": "explicit_artist", "detail": f"符合指定歌手 {artist}"}
            )
        if genre:
            item["score"] = round(float(item["score"]) + 0.6, 6)
            item["reasons"].append(
                {"code": "wiki_genre_relation", "detail": f"与流派 {genre} 的知识关系匹配"}
            )
        if track.id in recent_ids:
            item["score"] = round(float(item["score"]) - 0.2, 6)
            item["reasons"].append(
                {"code": "recently_played_penalty", "detail": "近期已播放，降低重复推荐优先级"}
            )
        local_ranked.append(item)
    local_ranked.sort(
        key=lambda item: (-float(item["score"]), str(item["track"].get("id") or ""))
    )
    local_ranked = _select_with_artist_diversity(local_ranked, requested_count)
    local_selected = local_ranked[: planned["local"]]

    cloud_goal = planned["cloud"] + max(0, planned["local"] - len(local_selected))
    cloud_items: list[dict[str, Any]] = []
    selected_videos: list[dict[str, Any]] = []
    cloud_errors: list[dict[str, Any]] = []
    local_identity = {_fold(f"{track.title} {track.author}") for track in catalog}
    local_title_terms = {
        _fold(str(track.title).strip("《》【】"))
        for track in catalog
        if _fold(str(track.title).strip("《》【】"))
    }
    seen_bvids: set[str] = set()
    seen_cloud_songs: set[str] = set()
    known_cloud_songs = [
        *normalized_seed_songs,
        *(str(song) for song in wiki.get("songs", []) if song),
    ]
    if cloud_goal > 0:
        queries = _cloud_queries(
            artist=artist,
            genre=genre,
            wiki=wiki,
            recent_profile=recent_profile,
            memory_seeds=memory_seeds,
            seed_songs=normalized_seed_songs,
            catalog_artists=list(
                dict.fromkeys(
                    str(item["track"].get("author") or "").strip()
                    for item in local_ranked
                    if str(item["track"].get("author") or "").strip()
                )
            ),
            limit=cloud_goal,
        )
        if cloud_search is None:
            cloud_errors.append(
                {
                    "source": "bilibili",
                    "error_code": "cloud_search_unavailable",
                    "message": "云端搜索能力不可用",
                    "retryable": False,
                    "attempts": 0,
                }
            )
        elif not queries:
            cloud_errors.append(
                {
                    "source": "bilibili",
                    "error_code": "no_cloud_seed",
                    "message": "没有足够的画像或知识库信息生成可靠的云端检索条件",
                    "retryable": False,
                    "attempts": 0,
                }
            )
        for cloud_query in queries:
            if len(cloud_items) >= cloud_goal:
                break
            try:
                result = cloud_search(cloud_query)
            except Exception as exc:
                result = {
                    "status": "error",
                    "error_code": "cloud_search_failed",
                    "error": str(exc),
                    "retryable": False,
                    "attempts": 1,
                }
            if result.get("status") == "error":
                cloud_errors.append(
                    {
                        "source": "bilibili",
                        "query": cloud_query,
                        "error_code": result.get("error_code") or "cloud_search_failed",
                        "message": result.get("error") or "云端搜索失败",
                        "retryable": bool(result.get("retryable")),
                        "attempts": int(result.get("attempts") or 1),
                    }
                )
                continue
            videos = [item for item in result.get("videos", []) if isinstance(item, dict)]
            videos.sort(key=lambda item: -int(item.get("play") or 0))
            for video in videos:
                bvid = str(video.get("bvid") or "").strip()
                identity = _fold(f"{video.get('title', '')} {video.get('author', '')}")
                folded_title = _fold(video.get("title"))
                song_identity = _song_identity_from_title(
                    video.get("title"), known_cloud_songs
                )
                if (
                    bvid in seen_bvids
                    or not _usable_cloud_video(video, artist=artist)
                    or identity in local_identity
                    or any(term in folded_title for term in local_title_terms)
                    or (song_identity and song_identity in seen_cloud_songs)
                ):
                    continue
                seen_bvids.add(bvid)
                if song_identity:
                    seen_cloud_songs.add(song_identity)
                selected_videos.append(video)
                cloud_items.append(
                    {
                        "track": {
                            "id": f"bilibili:{bvid}",
                            "title": str(video.get("title") or ""),
                            "author": str(video.get("author") or ""),
                            "duration": str(video.get("duration") or ""),
                            "bvid": bvid,
                            "url": f"https://www.bilibili.com/video/{bvid}",
                            "source_type": "bilibili",
                        },
                        "score": round(0.5 + min(int(video.get("play") or 0), 10_000_000) / 20_000_000, 6),
                        "reasons": [
                            {"code": "cloud_discovery", "detail": f"通过检索条件“{cloud_query}”找到的在线候选"}
                        ],
                    }
                )
                if len(cloud_items) >= cloud_goal:
                    break

    if len(cloud_items) < cloud_goal:
        needed = requested_count - len(cloud_items) - len(local_selected)
        if needed > 0:
            local_selected.extend(
                local_ranked[len(local_selected) : len(local_selected) + needed]
            )
    elif len(local_selected) < planned["local"]:
        cloud_items = cloud_items[: requested_count - len(local_selected)]

    selected = [*local_selected, *cloud_items]
    selected = selected[:requested_count]
    actual = {
        "local": sum(
            1 for item in selected if not str(item["track"].get("id") or "").startswith("bilibili:")
        ),
        "cloud": sum(
            1 for item in selected if str(item["track"].get("id") or "").startswith("bilibili:")
        ),
    }
    fallback_reasons: list[str] = []
    if actual["local"] < planned["local"]:
        fallback_reasons.append("local_candidates_insufficient")
    if actual["cloud"] < planned["cloud"]:
        fallback_reasons.append(
            "cloud_search_failed" if cloud_errors else "cloud_candidates_insufficient"
        )
    if len(selected) < requested_count:
        fallback_reasons.append("total_candidates_insufficient")

    constraints = {
        "requested_count": requested_count,
        "source_policy": source_policy,
        "planned": planned,
        "actual": actual,
        "fallback_reasons": fallback_reasons,
        "query": query.strip(),
        "artist": artist,
        "genre": genre,
        "seed_songs": normalized_seed_songs,
        "feedback_excluded_track_ids": sorted(feedback_excluded_ids),
        "wiki_status": wiki.get("status"),
    }
    batch = None
    if selected:
        batch = record_recommendation_batch(
            user_id=user_id,
            kind="conversation",
            scenario=scenario,
            current_track_id=None,
            profile_snapshot={
                "generated_at": profile["generated_at"],
                "policy_version": profile["policy_version"],
                "scenario": scenario,
                "window": recent_profile,
                "memory_seeds": memory_seeds,
            },
            constraints=constraints,
            items=selected,
        )

    notice = ""
    if "cloud_search_failed" in fallback_reasons:
        notice = (
            f"B站搜索暂时不可用，已将推荐动态调整为本地 {actual['local']} 首"
            + (f"、在线 {actual['cloud']} 首。" if actual["cloud"] else "。")
        )
    elif "local_candidates_insufficient" in fallback_reasons:
        notice = f"本地候选不足，已使用在线候选补足；本地 {actual['local']} 首、在线 {actual['cloud']} 首。"
    elif "cloud_candidates_insufficient" in fallback_reasons:
        notice = f"在线候选不足，已使用本地歌曲补足；本地 {actual['local']} 首、在线 {actual['cloud']} 首。"

    return {
        "status": "ok" if len(selected) == requested_count else ("partial" if selected else "empty"),
        "batch_id": batch["id"] if batch else None,
        "scenario": scenario,
        "source_policy": source_policy,
        "source_plan": {"planned": planned, "actual": actual},
        "fallback_reasons": fallback_reasons,
        "user_notice": notice,
        "track_ids": [str(item["track"]["id"]) for item in selected],
        "recommendations": selected,
        "videos": selected_videos[: actual["cloud"]],
        "cloud_errors": cloud_errors,
        "wiki_context": wiki,
        "requested_count": requested_count,
        "result_count": len(selected),
    }


def _stable_exploration_score(user_id: str, track_id: str, date_key: str) -> float:
    digest = hashlib.sha256(f"{user_id}:{date_key}:{track_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def _positive_score_map(items: Any, key: str) -> tuple[dict[str, float], float]:
    values: dict[str, float] = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            identity = str(item.get(key) or "").strip()
            score = float(item.get("score") or 0.0)
            if identity and score > 0:
                values[identity] = score
    return values, max(values.values(), default=1.0)


def _rank_candidate(
    track: Track,
    *,
    user_id: str,
    date_key: str,
    track_scores: dict[str, float],
    max_track_score: float,
    artist_scores: dict[str, float],
    max_artist_score: float,
    current_author: str,
) -> dict[str, Any]:
    reasons: list[dict[str, Any]] = [
        {"code": "local_available", "detail": "歌曲已在本地曲库中"}
    ]
    score = 0.1 * _stable_exploration_score(user_id, track.id, date_key)
    recent_track_score = track_scores.get(track.id, 0.0)
    if recent_track_score > 0:
        score += 0.35 * recent_track_score / max_track_score
        reasons.append(
            {
                "code": "recent_track_affinity",
                "detail": "近 7 天对这首歌存在正向行为",
                "evidence_score": round(recent_track_score, 6),
            }
        )
    recent_artist_score = artist_scores.get(track.author, 0.0)
    if track.author and recent_artist_score > 0:
        score += 0.45 * recent_artist_score / max_artist_score
        reasons.append(
            {
                "code": "recent_artist_affinity",
                "detail": f"近 7 天对歌手 {track.author} 的行为偏好较高",
                "evidence_score": round(recent_artist_score, 6),
            }
        )
    if current_author and track.author == current_author:
        score += 0.15
        reasons.append(
            {
                "code": "current_artist_continuity",
                "detail": f"与当前歌曲同为 {current_author}",
            }
        )
    if len(reasons) == 1:
        reasons.append(
            {"code": "catalog_exploration", "detail": "用于扩展本地曲库收听多样性"}
        )
    return {
        "track": track.model_dump(mode="json"),
        "score": round(score, 6),
        "reasons": reasons,
    }


def _select_with_artist_diversity(
    ranked: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    author_counts: dict[str, int] = {}
    for item in ranked:
        author = str(item["track"].get("author") or "")
        if author and author_counts.get(author, 0) >= 2:
            deferred.append(item)
            continue
        selected.append(item)
        if author:
            author_counts[author] = author_counts.get(author, 0) + 1
        if len(selected) >= limit:
            return selected
    for item in deferred:
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def recommend_local_radio_tracks(
    *,
    user_id: str,
    exclude_track_ids: list[str] | None = None,
    limit: int = 5,
    scenario: str = "默认",
    current_track_id: str | None = None,
) -> dict[str, Any]:
    requested_limit = max(1, min(int(limit), 20))
    excluded = {str(track_id) for track_id in (exclude_track_ids or []) if track_id}
    recent_ids = {
        str(entry["track"].get("id") or "")
        for entry in list_recent_tracks(user_id=user_id, limit=30)
    }
    feedback_excluded_ids = list_feedback_excluded_track_ids(user_id)
    catalog = scan_tracks()
    catalog_by_id = {track.id: track for track in catalog}
    candidates = [
        track
        for track in catalog
        if (
            track.id not in excluded
            and track.id not in recent_ids
            and track.id not in feedback_excluded_ids
        )
    ]

    profile = build_recent_preference_profile(
        user_id,
        catalog_tracks=catalog,
        scenario=scenario,
    )
    recent_profile = get_preference_window(profile, 7)
    track_scores, max_track_score = _positive_score_map(
        recent_profile.get("tracks"), "track_id"
    )
    artist_scores, max_artist_score = _positive_score_map(
        recent_profile.get("artists"), "author"
    )
    current = catalog_by_id.get(current_track_id or "")
    current_author = current.author if current else ""
    date_key = datetime.now(timezone.utc).date().isoformat()
    ranked = [
        _rank_candidate(
            track,
            user_id=user_id,
            date_key=date_key,
            track_scores=track_scores,
            max_track_score=max_track_score,
            artist_scores=artist_scores,
            max_artist_score=max_artist_score,
            current_author=current_author,
        )
        for track in candidates
    ]
    ranked.sort(
        key=lambda item: (-float(item["score"]), str(item["track"].get("id") or ""))
    )
    selected = _select_with_artist_diversity(ranked, requested_limit)
    reason_codes = [
        "local_only",
        "not_in_session",
        "not_recently_played",
        "negative_feedback_filtered",
        "recent_preference_ranked",
        "artist_diversity_applied",
    ]
    constraints = {
        "limit": requested_limit,
        "excluded_track_ids": sorted(excluded),
        "recent_track_ids": sorted(recent_ids),
        "feedback_excluded_track_ids": sorted(feedback_excluded_ids),
        "reason_codes": reason_codes,
    }
    batch = None
    if selected:
        batch = record_recommendation_batch(
            user_id=user_id,
            kind="radio",
            scenario=scenario,
            current_track_id=current_track_id,
            profile_snapshot={
                "generated_at": profile["generated_at"],
                "policy_version": profile["policy_version"],
                "window": recent_profile,
            },
            constraints=constraints,
            items=selected,
        )
    return {
        "batch_id": batch["id"] if batch else None,
        "tracks": [item["track"] for item in selected],
        "recommendations": selected,
        "reason_codes": reason_codes,
        "profile_window_days": 7,
        "excluded_count": len(excluded),
        "recent_excluded_count": len(recent_ids),
        "feedback_excluded_count": len(feedback_excluded_ids),
    }
