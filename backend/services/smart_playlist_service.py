"""Deterministic smart-playlist previews with local Tracks and remote candidates.

The LLM may translate natural language into this service's structured inputs,
but selection, filtering, persistence and warnings remain deterministic. A
preview is an immutable recommendation batch; it does not mutate playback or
create a named playlist until the user explicitly applies it.
"""

from __future__ import annotations

import subprocess
import threading
import math
import re
from pathlib import Path
from typing import Any, Callable

from models import Track
from services.music_metadata import normalize_artist, is_non_music_source
from services.memory_store import list_memory_items
from services.music_library_store import (
    append_playlist_batch,
    create_playlist_with_tracks,
    get_playlist,
    get_recommendation_batch,
    list_feedback_excluded_track_ids,
    list_recent_tracks,
    record_recommendation_batch,
)
from services.music_manager import find_track_by_id, find_track_by_bvid, resolve_music_path, scan_tracks
from services.preference_service import (
    build_recent_preference_profile,
    get_preference_window,
)
from services.recommendation_service import (
    _candidate_matches_scope,
    _fold,
    _memory_preference_seeds,
    _positive_score_map,
    _rank_candidate,
    _select_with_artist_diversity,
    _wiki_recommendation_context,
    _cloud_queries,
    _duration_seconds,
    _song_identity_from_title,
    _usable_cloud_video,
    _select_cloud_diversity,
    _source_targets,
    SourcePolicy,
    CloudSearch,
)


DurationProvider = Callable[[Track], float | None]

_DEFAULT_TRACK_SECONDS = 240.0
_DURATION_CACHE: dict[tuple[str, int, int], float | None] = {}
_DURATION_CACHE_LOCK = threading.Lock()
_VERSION_MARKERS = {
    "live": ("live", "现场", "演唱会"),
    "cover": ("cover", "翻唱"),
    "remix": ("remix", "混音", "dj版", "dj 版"),
    "instrumental": ("instrumental", "伴奏", "纯音乐"),
}
_VERSION_ALIASES = {
    "现场": "live",
    "现场版": "live",
    "翻唱": "cover",
    "翻唱版": "cover",
    "混音": "remix",
    "混音版": "remix",
    "伴奏": "instrumental",
    "纯音乐": "instrumental",
}


def _unique_text(values: list[str] | None, *, limit: int = 30) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = " ".join(str(value).split()).strip()
        key = _fold(normalized)
        if normalized and key and key not in seen:
            result.append(normalized)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def _normalized_versions(values: list[str] | None) -> list[str]:
    versions: list[str] = []
    for value in _unique_text(values, limit=10):
        normalized = _VERSION_ALIASES.get(value.casefold(), value.casefold())
        if normalized in _VERSION_MARKERS and normalized not in versions:
            versions.append(normalized)
    return versions


def _matches_artist(track: Track, artists: list[str]) -> bool:
    searchable = _fold(f"{track.author} {track.title} {track.filename}")
    return any(_fold(artist) in searchable for artist in artists)


def _matches_version(track: Track, versions: list[str]) -> bool:
    searchable = f"{track.title} {track.filename}".casefold()
    return any(
        marker in searchable
        for version in versions
        for marker in _VERSION_MARKERS.get(version, ())
    )


def _memory_constraints(user_id: str, scenario: str) -> dict[str, list[str]]:
    result = {
        "excluded_artists": [],
        "excluded_songs": [],
        "excluded_versions": [],
        "unsupported_directives": [],
    }
    for item in list_memory_items(
        user_id,
        scenario=scenario or "默认",
        include_global=True,
        limit=50,
    ):
        if str(item.get("kind") or "") != "avoidance":
            continue
        key = str(item.get("memory_key") or "").strip()
        prefix, separator, value = key.partition(":")
        target = {
            "artist": "excluded_artists",
            "performer": "excluded_artists",
            "song": "excluded_songs",
            "track": "excluded_songs",
            "version": "excluded_versions",
        }.get(prefix.casefold())
        if separator and value.strip() and target:
            result[target].append(value.strip())
        else:
            directive = str(item.get("directive") or "").strip()
            if directive:
                result["unsupported_directives"].append(directive)
    result["excluded_versions"] = _normalized_versions(result["excluded_versions"])
    return result


def _probe_duration(track: Track) -> float | None:
    resolved = resolve_music_path(track.id)
    if not resolved:
        return None
    path = Path(resolved)
    try:
        stat = path.stat()
        cache_key = (str(path), int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        return None
    with _DURATION_CACHE_LOCK:
        if cache_key in _DURATION_CACHE:
            return _DURATION_CACHE[cache_key]
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        value = float(completed.stdout.strip()) if completed.returncode == 0 else 0.0
        duration = value if value > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        duration = None
    with _DURATION_CACHE_LOCK:
        _DURATION_CACHE[cache_key] = duration
    return duration


def _select_for_duration(
    ranked: list[dict[str, Any]],
    target_seconds: float,
    duration_provider: DurationProvider,
) -> tuple[list[dict[str, Any]], bool]:
    selected: list[dict[str, Any]] = []
    total = 0.0
    estimated = False
    for item in ranked[:50]:
        raw = item["track"]
        measured = (_duration_seconds(raw.get("duration"))
                    if raw.get("source_type") == "bilibili"
                    else duration_provider(Track.model_validate(raw)))
        duration = measured if measured and measured > 0 else _DEFAULT_TRACK_SECONDS
        estimated = estimated or measured is None or measured <= 0
        before = total
        after = total + duration
        if selected and before < target_seconds <= after:
            if abs(target_seconds - before) < abs(after - target_seconds):
                break
        selected.append({**item, "duration_seconds": round(duration, 3)})
        total = after
        if total >= target_seconds:
            break
    return selected, estimated


def generate_smart_playlist(
    *,
    user_id: str,
    scenario: str = "默认",
    count: int | None = None,
    duration_minutes: float | None = None,
    query: str = "",
    include_artists: list[str] | None = None,
    exclude_artists: list[str] | None = None,
    genre: str = "",
    mood: str = "",
    language: str = "",
    exclude_versions: list[str] | None = None,
    energy_curve: str = "",
    duration_provider: DurationProvider | None = None,
    source_policy: SourcePolicy = "balanced",
    cloud_search: CloudSearch | None = None,
    target_playlist_id: str = "",
) -> dict[str, Any]:
    """Generate an immutable mixed preview; remote candidates are never local Tracks."""
    if source_policy not in {"balanced", "local", "cloud"}:
        raise ValueError("unsupported source_policy")
    target = get_playlist(target_playlist_id, user_id) if target_playlist_id else None
    if target_playlist_id and target is None:
        raise LookupError("target playlist not found")
    existing_tracks = [item["track"] for item in target["items"]] if target else []
    existing_ids = {track["id"] for track in existing_tracks}
    existing_bvids = {track.get("bvid") for track in existing_tracks if track.get("bvid")}
    scenario = scenario.strip()[:80] or "默认"
    requested_count = None if count is None else max(1, min(int(count), 50))
    target_seconds = (
        max(60.0, min(float(duration_minutes), 12 * 60.0) * 60.0)
        if duration_minutes is not None and float(duration_minutes) > 0
        else None
    )
    if requested_count is None and target_seconds is None:
        requested_count = 3

    included = _unique_text(include_artists)
    excluded = _unique_text(exclude_artists)
    versions = _normalized_versions(exclude_versions)
    memory = _memory_constraints(user_id, scenario)
    excluded = _unique_text([*excluded, *memory["excluded_artists"]])
    versions = list(dict.fromkeys([*versions, *memory["excluded_versions"]]))
    excluded_songs = {_fold(value) for value in memory["excluded_songs"] if _fold(value)}

    warnings: list[dict[str, str]] = []
    unsupported = {
        "mood": mood.strip(),
        "language": language.strip(),
        "energy_curve": energy_curve.strip(),
    }
    for field, value in unsupported.items():
        if value and value.casefold() not in {"none", "无", "不限"}:
            warnings.append(
                {
                    "code": f"{field}_metadata_unavailable",
                    "message": f"当前曲库缺少可核验的{ {'mood': '情绪', 'language': '语言', 'energy_curve': '能量曲线'}[field] }特征，本次未对“{value}”做强过滤。",
                }
            )
    if memory["unsupported_directives"]:
        warnings.append(
            {
                "code": "memory_constraint_not_machine_readable",
                "message": "部分长期回避要求尚不能映射为歌曲元数据，已保留在审计信息中。",
            }
        )

    catalog = scan_tracks()
    feedback_excluded = list_feedback_excluded_track_ids(user_id)
    recent_ids = {
        str(item.get("track", {}).get("id") or "")
        for item in list_recent_tracks(user_id=user_id, limit=30)
    }
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
    preference_seeds = _memory_preference_seeds(user_id, scenario)
    genre = genre.strip()
    wiki = _wiki_recommendation_context(
        [genre, *included, *preference_seeds["artists"], *preference_seeds["genres"]]
    )
    genre_wiki = _wiki_recommendation_context([genre]) if genre else wiki
    enforce_genre = bool(genre and (genre_wiki.get("artists") or genre_wiki.get("songs")))
    if genre and not enforce_genre:
        warnings.append(
            {
                "code": "genre_evidence_unavailable",
                "message": f"知识库没有足够证据校验流派“{genre}”，本次仅将其作为排序上下文。",
            }
        )

    from datetime import datetime, timezone

    date_key = datetime.now(timezone.utc).date().isoformat()
    ranked: list[dict[str, Any]] = []
    for track in catalog:
        searchable = _fold(f"{track.title} {track.author} {track.filename}")
        if track.id in feedback_excluded or track.id in existing_ids or (track.bvid and track.bvid in existing_bvids):
            continue
        if included and not _matches_artist(track, included):
            continue
        if excluded and _matches_artist(track, excluded):
            continue
        if excluded_songs and any(song in searchable for song in excluded_songs):
            continue
        if versions and _matches_version(track, versions):
            continue
        if enforce_genre and not _candidate_matches_scope(
            track, artist="", genre=genre, wiki=genre_wiki
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
        if included:
            item["score"] = round(float(item["score"]) + 0.8, 6)
            item["reasons"].append(
                {"code": "included_artist", "detail": "符合指定歌手范围"}
            )
        if track.author and any(
            _fold(seed) in _fold(track.author)
            for seed in preference_seeds["artists"]
        ):
            item["score"] = round(float(item["score"]) + 0.3, 6)
            item["reasons"].append(
                {
                    "code": "long_term_artist_preference",
                    "detail": f"命中当前场景或全局长期歌手偏好：{track.author}",
                }
            )
        if any(_fold(seed) in _fold(track.title) for seed in preference_seeds["songs"]):
            item["score"] = round(float(item["score"]) + 0.35, 6)
            item["reasons"].append(
                {"code": "long_term_song_preference", "detail": "命中长期歌曲偏好"}
            )
        if track.id in recent_ids:
            item["score"] = round(float(item["score"]) - 0.2, 6)
            item["reasons"].append(
                {"code": "recently_played_penalty", "detail": "近期已播放，降低重复优先级"}
            )
        ranked.append(item)

    ranked.sort(key=lambda item: (-float(item["score"]), str(item["track"]["id"])))
    ranked = _select_with_artist_diversity(ranked, len(ranked))
    goal = requested_count or min(50, max(1, math.ceil((target_seconds or 720) / 180)))
    if source_policy == "local" and target_seconds and requested_count is None:
        goal = min(50, len(ranked))
    planned = _source_targets(goal, source_policy)
    local_selected = ranked[:planned["local"]]
    cloud_goal = planned["cloud"] + (max(0, planned["local"] - len(local_selected)) if source_policy == "balanced" else 0)
    cloud_items: list[dict[str, Any]] = []
    cloud_errors: list[dict[str, Any]] = []
    if cloud_goal:
        from services.bili_client import search_cloud_candidates
        search = cloud_search or search_cloud_candidates
        queries: list[str] = []
        for artist in included or [""]:
            queries.extend(_cloud_queries(
                artist=artist, genre=genre, wiki=wiki, recent_profile=recent_profile,
                memory_seeds=preference_seeds, seed_songs=preference_seeds["songs"],
                catalog_artists=list(dict.fromkeys(t.author for t in catalog if t.author)),
                limit=min(cloud_goal, 6),
            ))
        if not queries and query.strip():
            queries = [query.strip()]
        seen_bvids = {t.bvid for t in catalog if t.bvid} | {t.get("bvid") for t in existing_tracks if t.get("bvid")}
        known_songs = [*wiki.get("songs", []), *preference_seeds["songs"], *(t.title for t in catalog)]
        seen_songs = {_song_identity_from_title(t.title, known_songs) for t in catalog}
        seen_songs.update(_song_identity_from_title(t.get("title", ""), known_songs) for t in existing_tracks)
        artist_seeds = [*included, *wiki.get("artists", []), *preference_seeds["artists"], *(t.author for t in catalog)]
        for term in list(dict.fromkeys(queries))[:4]:
            try:
                result = search(term)
            except Exception:
                result = {"status": "error", "error": "联网搜索暂时不可用"}
            if result.get("status") == "error":
                cloud_errors.append({"query": term, "message": result.get("error") or "联网搜索失败"})
                continue
            videos = [v.model_dump(mode="json") if hasattr(v, "model_dump") else v for v in result.get("videos", [])][:30]
            for video in sorted((v for v in videos if isinstance(v, dict)), key=lambda v: -int(v.get("play") or 0)):
                bvid = str(video.get("bvid") or "")
                title = str(video.get("title") or "")
                searchable = _fold(title)
                identity = _song_identity_from_title(title, known_songs)
                if (bvid in seen_bvids or identity in seen_songs or not _usable_cloud_video(video)
                    or (included and not any(_fold(a) in searchable for a in included))
                    or any(_fold(a) in searchable for a in excluded)
                    or any(song in searchable for song in excluded_songs)
                    or _matches_version(Track(id=bvid, title=title, author="", date="", filename=title, subDir="", size=0, url=""), versions)
                    or (enforce_genre and not any(_fold(v) in searchable for v in [*genre_wiki.get("artists", []), *genre_wiki.get("songs", [])]))):
                    continue
                performer = next((a for value in artist_seeds if (a := normalize_artist(value)) and _fold(a) in searchable), "")
                song_match = re.search(r"《([^》]+)》", title)
                song = song_match.group(1) if song_match else next((s for s in known_songs if s and _fold(s) in searchable), title)
                seen_bvids.add(bvid)
                seen_songs.add(identity)
                cloud_items.append({"track": {
                    "id": f"bilibili:{bvid}", "title": song, "author": performer,
                    "uploader": str(video.get("author") or ""), "video_title": title,
                    "duration": str(video.get("duration") or ""), "bvid": bvid,
                    "url": f"https://www.bilibili.com/video/{bvid}", "source_type": "bilibili",
                }, "score": 0.5, "search_query": term, "reasons": [{"code": "cloud_discovery", "detail": f"通过“{term}”找到的待下载歌曲"}]})
        if not queries:
            cloud_errors.append({"message": "没有足够的歌手、歌曲或知识库信息生成联网检索条件"})
        cloud_items = _select_cloud_diversity(cloud_items, cloud_goal)
        if len(cloud_items) < cloud_goal:
            warnings.append({"code": "cloud_search_failed" if cloud_errors else "cloud_candidates_insufficient", "message": "联网候选不足或搜索失败，已按可用来源调整比例。"})
    if source_policy == "balanced" and len(cloud_items) < cloud_goal:
        local_selected = ranked[:goal - len(cloud_items)]
    ranked = [*local_selected, *cloud_items]
    provider = duration_provider or _probe_duration
    used_estimated_duration = False
    if target_seconds is not None and requested_count is None:
        selected, used_estimated_duration = _select_for_duration(
            ranked, target_seconds, provider
        )
    else:
        selected = ranked[: requested_count or len(ranked)]
        with_duration: list[dict[str, Any]] = []
        for item in selected:
            raw = item["track"]
            measured = (_duration_seconds(raw.get("duration")) if raw.get("source_type") == "bilibili"
                        else provider(Track.model_validate(raw)))
            duration = measured if measured and measured > 0 else _DEFAULT_TRACK_SECONDS
            used_estimated_duration = used_estimated_duration or measured is None or measured <= 0
            with_duration.append({**item, "duration_seconds": round(duration, 3)})
        selected = with_duration

    total_duration = sum(float(item.get("duration_seconds") or 0) for item in selected)
    if used_estimated_duration:
        warnings.append(
            {
                "code": "duration_estimated",
                "message": "部分歌曲无法读取精确时长，已按每首 4 分钟估算；保存或播放前请查看实际时长。",
            }
        )
    if requested_count is not None and len(selected) < requested_count:
        warnings.append(
            {
                "code": "candidate_count_insufficient",
                "message": f"符合强约束的歌曲只有 {len(selected)} 首，少于请求的 {requested_count} 首。",
            }
        )
    if target_seconds is not None and selected:
        deviation = abs(total_duration - target_seconds) / target_seconds
        if deviation > 0.2:
            warnings.append(
                {
                    "code": "duration_target_deviation",
                    "message": f"当前结果约 {round(total_duration / 60)} 分钟，与目标 {round(target_seconds / 60)} 分钟存在偏差。",
                }
            )

    constraints = {
        "target_playlist_id": target_playlist_id,
        "target_revision": target["revision"] if target else None,
        "target_anchor_item_id": target["items"][-1]["id"] if target and target["items"] else "",
        "query": query.strip(),
        "scenario": scenario,
        "requested_count": requested_count,
        "duration_minutes": round(target_seconds / 60, 2) if target_seconds else None,
        "include_artists": included,
        "exclude_artists": excluded,
        "genre": genre,
        "mood": mood.strip(),
        "language": language.strip(),
        "exclude_versions": versions,
        "energy_curve": energy_curve.strip(),
        "source_policy": source_policy,
        "source_plan": {"planned": planned, "actual": {
            "local": sum(i["track"].get("source_type") != "bilibili" for i in selected),
            "cloud": sum(i["track"].get("source_type") == "bilibili" for i in selected),
        }},
        "cloud_errors": cloud_errors,
        "feedback_excluded_track_ids": sorted(feedback_excluded),
        "memory_unsupported_directives": memory["unsupported_directives"],
        "wiki_status": wiki.get("status"),
        "warnings": warnings,
    }
    batch = None
    if selected:
        batch = record_recommendation_batch(
            user_id=user_id,
            kind="smart_playlist",
            scenario=scenario,
            current_track_id=None,
            profile_snapshot={
                "generated_at": profile["generated_at"],
                "policy_version": profile["policy_version"],
                "window": recent_profile,
                "memory_seeds": preference_seeds,
            },
            constraints=constraints,
            items=selected,
        )

    return {
        "target_playlist_id": target_playlist_id,
        "status": "empty" if not selected else ("partial" if warnings else "ok"),
        "batch_id": batch["id"] if batch else None,
        "scenario": scenario,
        "suggested_name": f"{scenario}智能歌单",
        "tracks": [item["track"] for item in selected if item["track"].get("source_type") != "bilibili"],
        "remote_candidates": [item["track"] for item in selected if item["track"].get("source_type") == "bilibili"],
        "source_plan": constraints["source_plan"],
        "cloud_errors": cloud_errors,
        "recommendations": selected,
        "result_count": len(selected),
        "estimated_duration_seconds": round(total_duration, 3),
        "duration_is_estimated": used_estimated_duration or any(i["track"].get("source_type") == "bilibili" for i in selected),
        "constraints": constraints,
        "warnings": warnings,
    }


def save_smart_playlist_preview(
    *,
    batch_id: str,
    name: str,
    user_id: str,
    description: str = "",
    schedule: bool = True,
    target_playlist_id: str = "",
) -> dict[str, Any]:
    """Save local items now and queue remote downloads into this exact playlist."""
    batch = get_recommendation_batch(batch_id, user_id=user_id)
    if batch is None or batch.get("kind") != "smart_playlist":
        raise LookupError("smart playlist preview not found")
    constraints = batch.get("constraints") or {}
    bound_target = str(constraints.get("target_playlist_id") or "")
    if bound_target != target_playlist_id:
        raise ValueError("preview target does not match requested playlist; generate a new preview")
    if target_playlist_id and get_playlist(target_playlist_id, user_id) is None:
        raise LookupError("target playlist not found")
    target_before = get_playlist(target_playlist_id, user_id) if target_playlist_id else None
    tracks: list[dict[str, Any]] = []
    missing: list[str] = []
    remote_items: list[dict[str, Any]] = []
    selection_order: list[str] = []
    for item in batch.get("items") or []:
        track_id = str(item.get("track_id") or "")
        raw = item.get("track") or {}
        if track_id.startswith("bilibili:"):
            if is_non_music_source(str(raw.get("video_title") or raw.get("title") or "")):
                raise ValueError("草稿包含非音乐联网候选，请先移除或替换后再确认")
            bvid = str(raw.get("bvid") or track_id.split(":", 1)[1])
            selection_order.append(f"bvid:{bvid}")
            canonical = find_track_by_bvid(bvid)
            if canonical:
                tracks.append(canonical.model_dump(mode="json"))
            else:
                remote_items.append({"bvid": bvid, "title": raw.get("title") or "",
                    "artist": raw.get("author") or "", "uploader": raw.get("uploader") or "",
                    "video_title": raw.get("video_title") or raw.get("title") or ""})
            continue
        canonical = find_track_by_id(track_id)
        if canonical is None:
            missing.append(track_id)
        else:
            tracks.append(canonical.model_dump(mode="json"))
            selection_order.append(f"bvid:{canonical.bvid}" if canonical.bvid else f"id:{canonical.id}")
    if missing:
        raise ValueError("preview contains local tracks that are no longer available")
    if not tracks and not remote_items:
        raise ValueError("smart playlist preview is empty")
    summary = description.strip() or (
        f"由智能歌单预览 {batch_id[:8]} 生成；场景：{batch.get('scenario') or '默认'}"
    )
    # Validate sources before creating a durable playlist.
    from services.download_job_service import _normalize_items, create_download_job
    if remote_items:
        _normalize_items(remote_items)
    if target_playlist_id:
        playlist = append_playlist_batch(
            target_playlist_id, tracks=tracks, batch_id=batch_id,
            expected_revision=constraints.get("target_revision"), user_id=user_id,
        )
    else:
        playlist = create_playlist_with_tracks(
            name, tracks, description=summary, user_id=user_id, smart_batch_id=batch_id,
        )
        playlist = {**playlist, "added_count": len(tracks)}
    if remote_items:
        job = create_download_job(user_id=user_id, items=remote_items,
            target_playlist_id=playlist["id"], idempotency_key=f"smart-playlist:{batch_id}", schedule=schedule,
            playlist_order={"items": selection_order,
                "anchor": constraints.get("target_anchor_item_id", target_before["items"][-1]["id"] if target_before and target_before["items"] else "")})
        return {**playlist, "download_job": job, "import_status": job["status"]}
    return {**playlist, "download_job": None, "import_status": "completed"}
