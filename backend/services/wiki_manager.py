import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import PROJECT_ROOT, settings

WIKI_TEMPLATE_DIR = PROJECT_ROOT / "template" / "wiki"
CURRENT_WIKI_SCHEMA_VERSION = "3.0"
ENTITY_TYPES = ("songs", "artists", "genres", "albums")
WIKI_RESET_MANIFEST_DIR = PROJECT_ROOT / "db" / "wiki-reset-manifests"
_WIKI_RESET_LOCK = threading.RLock()


def _ensure_dirs(wiki_dir: str) -> None:
    """Create all required wiki subdirectories."""
    dirs = [
        os.path.join(wiki_dir, "raw", "songs"),
        os.path.join(wiki_dir, "wiki", "entities", "songs"),
        os.path.join(wiki_dir, "wiki", "entities", "artists"),
        os.path.join(wiki_dir, "wiki", "entities", "genres"),
        os.path.join(wiki_dir, "wiki", "entities", "albums"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)


def _ensure_file(path: str, content: str) -> None:
    """Write file only if it doesn't exist."""
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


def _read_frontmatter(content: str) -> Dict[str, str]:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    result: Dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip()] = value.strip().strip('"')
    return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_raw_sources(wiki_dir: Path) -> list[dict[str, str]]:
    """Read only the identity fields needed to reconnect sources after a reset."""
    raw_dir = wiki_dir / "raw" / "songs"
    if not raw_dir.exists():
        return []

    sources: list[dict[str, str]] = []
    identity_fields = (
        "source_id",
        "bvid",
        "linked_song",
        "local_relative_path",
        "audio_sha256",
    )
    for page in sorted(raw_dir.glob("*.md")):
        metadata = _read_frontmatter(page.read_text(encoding="utf-8", errors="replace"))
        identity = {field: metadata.get(field, "") for field in identity_fields}
        identity["raw_record"] = page.name
        sources.append(identity)
    return sources


def build_wiki_recovery_manifest(
    wiki_dir: Optional[str] = None,
    music_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a portable identity manifest without copying audio or Wiki prose."""
    wiki_path = Path(wiki_dir or settings.WIKI_DIR).resolve()
    music_path = Path(music_dir or settings.MUSIC_DIR).resolve()
    previous_sources = _existing_raw_sources(wiki_path)
    previous_by_bvid = {
        source["bvid"]: source for source in previous_sources if source.get("bvid")
    }
    previous_by_path = {
        source["local_relative_path"]: source
        for source in previous_sources
        if source.get("local_relative_path")
    }

    from services.music_manager import scan_tracks

    tracks = []
    for track in scan_tracks(str(music_path)):
        relative_path = Path(track.id).as_posix()
        previous = previous_by_bvid.get(track.bvid or "") or previous_by_path.get(relative_path) or {}
        audio_path = (music_path / Path(track.id)).resolve()
        audio_sha256 = previous.get("audio_sha256", "")
        # A BVID is already a stable source identity. Hash BVID-less local files so
        # they can still be reconnected after a rename.
        if not track.bvid and not audio_sha256 and audio_path.is_file():
            audio_sha256 = _sha256_file(audio_path)
        source_id = (
            f"bilibili:{track.bvid}"
            if track.bvid
            else f"local:{audio_sha256}" if audio_sha256 else f"local-path:{relative_path}"
        )
        tracks.append(
            {
                "track_id": relative_path,
                "source_id": source_id,
                "bvid": track.bvid or "",
                "title": track.title,
                "artist": track.author,
                "local_relative_path": relative_path,
                "audio_sha256": audio_sha256,
                "audio_size_bytes": track.size,
                "previous_linked_song": previous.get("linked_song", ""),
            }
        )

    previous_status = get_wiki_status(str(wiki_path))
    previous_status.pop("wiki_dir", None)
    return {
        "manifest_version": 1,
        "created_at": datetime.now().astimezone().isoformat(),
        "previous_wiki_status": previous_status,
        "track_count": len(tracks),
        "tracks": tracks,
        "previous_wiki_source_count": len(previous_sources),
        "previous_wiki_sources": previous_sources,
    }


def _validate_reset_target(wiki_path: Path, music_path: Path) -> None:
    """Refuse broad or overlapping targets before recursive deletion."""
    project_path = PROJECT_ROOT.resolve()
    forbidden = {Path(wiki_path.anchor), Path.home().resolve(), project_path, music_path}
    if wiki_path in forbidden:
        raise ValueError(f"拒绝重置不安全的 Wiki 路径：{wiki_path}")
    try:
        music_path.relative_to(wiki_path)
    except ValueError:
        pass
    else:
        raise ValueError("Wiki 目录包含本地音乐目录，拒绝重置")
    try:
        wiki_path.relative_to(music_path)
    except ValueError:
        pass
    else:
        raise ValueError("Wiki 目录位于本地音乐目录内，拒绝重置")


def reset_wiki(
    wiki_dir: Optional[str] = None,
    music_dir: Optional[str] = None,
    manifest_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Safely reset generated Wiki data while preserving all local audio files."""
    wiki_path = Path(wiki_dir or settings.WIKI_DIR).resolve()
    music_path = Path(music_dir or settings.MUSIC_DIR).resolve()
    manifest_root = Path(manifest_dir or WIKI_RESET_MANIFEST_DIR).resolve()
    _validate_reset_target(wiki_path, music_path)
    try:
        manifest_root.relative_to(wiki_path)
    except ValueError:
        pass
    else:
        raise ValueError("恢复清单目录不能位于将被重置的 Wiki 目录内")

    with _WIKI_RESET_LOCK:
        from services.wiki_sync import cancel_wiki_enrichment
        cancel_wiki_enrichment(str(wiki_path))
        manifest = build_wiki_recovery_manifest(str(wiki_path), str(music_path))
        manifest_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        manifest_path = manifest_root / f"wiki-reset-{timestamp}.json"
        temporary_manifest = manifest_path.with_suffix(".json.tmp")
        temporary_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_manifest.replace(manifest_path)

        backup_path = wiki_path.parent / f".{wiki_path.name}.reset-{uuid.uuid4().hex}"
        had_existing_wiki = wiki_path.exists()
        if had_existing_wiki:
            wiki_path.rename(backup_path)

        try:
            result = init_wiki(str(wiki_path))
        except Exception:
            if wiki_path.exists():
                shutil.rmtree(wiki_path)
            if backup_path.exists():
                backup_path.rename(wiki_path)
            raise
        else:
            if backup_path.exists():
                shutil.rmtree(backup_path)

        status = get_wiki_status(str(wiki_path))
        try:
            manifest_display_path = manifest_path.relative_to(PROJECT_ROOT.resolve()).as_posix()
        except ValueError:
            manifest_display_path = str(manifest_path)
        return {
            "status": "reset",
            "wiki_initialized": result.get("status") in {"initialized", "already_initialized"},
            "schema_version": status.get("schema_version"),
            "preserved_local_tracks": manifest["track_count"],
            "previous_wiki_sources": manifest["previous_wiki_source_count"],
            "recovery_manifest": manifest_display_path,
            "local_music_deleted": False,
        }


def _entity_pages(wiki_dir: str) -> List[Path]:
    entity_root = Path(wiki_dir) / "wiki" / "entities"
    if not entity_root.exists():
        return []
    return sorted(path for path in entity_root.rglob("*.md") if path.is_file())


def audit_wiki_quality(wiki_dir: Optional[str] = None, max_issues: int = 500) -> Dict:
    """Read-only audit of provenance, portability and graph integrity."""
    wiki_dir = wiki_dir or settings.WIKI_DIR
    root = Path(wiki_dir)
    if not (root / ".wiki-schema.md").exists():
        return {"initialized": False, "issues": []}

    pages = _entity_pages(wiki_dir)
    known_entities = {page.stem for page in pages}
    issues = []
    issue_counts: Dict[str, int] = {}
    severity_counts: Dict[str, int] = {}
    verification_counts = {
        "verified": 0,
        "inferred": 0,
        "needs_review": 0,
        "missing": 0,
        "invalid": 0,
    }
    issue_total = 0

    def add_issue(code: str, severity: str, path: Path, message: str) -> None:
        nonlocal issue_total
        issue_total += 1
        issue_counts[code] = issue_counts.get(code, 0) + 1
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
        if len(issues) < max_issues:
            issues.append({
                "code": code,
                "severity": severity,
                "path": path.relative_to(root).as_posix(),
                "message": message,
            })

    legacy_entities = 0
    for page in pages:
        content = page.read_text(encoding="utf-8", errors="replace")
        metadata = _read_frontmatter(content)
        if metadata.get("schema_version") != CURRENT_WIKI_SCHEMA_VERSION:
            legacy_entities += 1
            add_issue("legacy_schema", "high", page, "实体尚未迁移到 Schema v3。")
        verification_status = metadata.get("verification_status", "")
        if not verification_status:
            verification_counts["missing"] += 1
            add_issue("missing_verification_status", "high", page, "缺少核验状态。")
        elif verification_status not in {"verified", "inferred", "needs_review"}:
            verification_counts["invalid"] += 1
            add_issue(
                "invalid_verification_status",
                "high",
                page,
                f"未知核验状态：{verification_status}",
            )
        else:
            verification_counts[verification_status] += 1
        if not metadata.get("confidence"):
            add_issue("missing_confidence", "medium", page, "缺少置信度。")
        if "## Evidence" not in content:
            add_issue("missing_evidence", "high", page, "缺少可追溯证据章节。")
        if re.search(r"(?:^|\W)Unknown(?:$|\W)", content, re.IGNORECASE):
            add_issue("unknown_placeholder", "medium", page, "包含旧版 Unknown 占位值。")
        for link in re.findall(r"\[\[([^\]|#]+)", content):
            if link not in known_entities:
                add_issue("broken_entity_link", "medium", page, f"实体链接不存在：{link}")

    raw_pages = sorted((root / "raw" / "songs").glob("*.md")) if (root / "raw" / "songs").exists() else []
    legacy_raw_records = 0
    for page in raw_pages:
        content = page.read_text(encoding="utf-8", errors="replace")
        metadata = _read_frontmatter(content)
        if metadata.get("schema_version") != CURRENT_WIKI_SCHEMA_VERSION:
            legacy_raw_records += 1
            add_issue("legacy_raw_schema", "high", page, "原始记录尚未迁移到 Schema v3。")
        if re.search(r"(?im)^(?:audio_file_path|local_relative_path):\s*[A-Za-z]:[\\/]", content):
            add_issue("absolute_local_path", "medium", page, "包含机器相关的绝对路径。")
        for required in ("source_id", "audio_sha256", "duration_source"):
            if required not in metadata:
                add_issue("missing_raw_provenance", "medium", page, f"缺少原始来源字段：{required}")

    high_severity_count = severity_counts.get("high", 0)
    all_entities_verified = bool(pages) and verification_counts["verified"] == len(pages)
    all_entities_grounded = bool(pages) and not any(
        verification_counts[key] for key in ("needs_review", "missing", "invalid")
    )
    return {
        "initialized": True,
        "schema_version": get_wiki_status(wiki_dir)["schema_version"],
        "expected_schema_version": CURRENT_WIKI_SCHEMA_VERSION,
        "entity_pages": len(pages),
        "raw_records": len(raw_pages),
        "legacy_entities": legacy_entities,
        "legacy_raw_records": legacy_raw_records,
        "issue_counts": issue_counts,
        "severity_counts": severity_counts,
        "issues": issues,
        "issue_total": issue_total,
        "issues_truncated": issue_total > len(issues),
        "verification_counts": verification_counts,
        "structurally_valid": high_severity_count == 0,
        "ready_for_inferred_answers": high_severity_count == 0 and all_entities_grounded,
        "ready_for_verified_answers": high_severity_count == 0 and all_entities_verified,
    }


def init_wiki(wiki_dir: Optional[str] = None) -> Dict:
    """Initialize the wiki directory structure by copying from template."""
    wiki_dir = wiki_dir or settings.WIKI_DIR
    wiki_path = Path(wiki_dir)

    if wiki_path.exists() and os.path.exists(os.path.join(wiki_dir, ".wiki-schema.md")):
        return {"status": "already_initialized", "wiki_dir": wiki_dir}

    _ensure_dirs(wiki_dir)

    # Copy template files to wiki directory
    created_date = datetime.now().strftime("%Y-%m-%d")
    for tpl_file in WIKI_TEMPLATE_DIR.iterdir():
        if tpl_file.is_file() and not tpl_file.name.startswith("."):
            dest = os.path.join(wiki_dir, tpl_file.name)
            if not os.path.exists(dest):
                content = tpl_file.read_text(encoding="utf-8")
                content = content.replace("{created}", created_date)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(content)

    # Copy .wiki-schema.md (hidden file)
    schema_tpl = WIKI_TEMPLATE_DIR / ".wiki-schema.md"
    schema_dest = os.path.join(wiki_dir, ".wiki-schema.md")
    if schema_tpl.exists() and not os.path.exists(schema_dest):
        content = schema_tpl.read_text(encoding="utf-8")
        content = content.replace("{created}", created_date)
        with open(schema_dest, "w", encoding="utf-8") as f:
            f.write(content)

    # Write .wiki-cache.json
    cache_path = os.path.join(wiki_dir, ".wiki-cache.json")
    _ensure_file(
        cache_path,
        json.dumps(
            {"version": 2, "schema_version": "3.0", "pipeline_version": "evidence-v1", "entries": {}},
            indent=2,
        ),
    )

    return {"status": "initialized", "wiki_dir": wiki_dir}


def get_wiki_status(wiki_dir: Optional[str] = None) -> Dict:
    """Return wiki initialization status and statistics."""
    wiki_dir = wiki_dir or settings.WIKI_DIR
    schema_path = os.path.join(wiki_dir, ".wiki-schema.md")

    if not os.path.exists(schema_path):
        return {"initialized": False}

    schema_content = Path(schema_path).read_text(encoding="utf-8")
    version_match = re.search(r"^- version:\s*([^\s]+)", schema_content, re.MULTILINE)
    schema_version = version_match.group(1) if version_match else "unknown"

    songs_dir = os.path.join(wiki_dir, "wiki", "entities", "songs")
    artists_dir = os.path.join(wiki_dir, "wiki", "entities", "artists")
    genres_dir = os.path.join(wiki_dir, "wiki", "entities", "genres")
    albums_dir = os.path.join(wiki_dir, "wiki", "entities", "albums")

    def count_md_files(d: str) -> int:
        if not os.path.exists(d):
            return 0
        return len([f for f in os.listdir(d) if f.endswith(".md")])

    # Find last ingested time from log.md
    last_ingested = None
    log_path = os.path.join(wiki_dir, "log.md")
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in reversed(lines):
                if line.startswith("|") and "ingest" in line.lower():
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 2 and parts[1]:
                        last_ingested = parts[1]
                        break

    legacy_entity_count = sum(
        1
        for page in _entity_pages(wiki_dir)
        if _read_frontmatter(page.read_text(encoding="utf-8", errors="replace")).get("schema_version")
        != CURRENT_WIKI_SCHEMA_VERSION
    )
    raw_dir = Path(wiki_dir) / "raw" / "songs"
    legacy_raw_count = sum(
        1
        for page in raw_dir.glob("*.md")
        if _read_frontmatter(page.read_text(encoding="utf-8", errors="replace")).get("schema_version")
        != CURRENT_WIKI_SCHEMA_VERSION
    ) if raw_dir.exists() else 0

    return {
        "initialized": True,
        "schema_version": schema_version,
        "expected_schema_version": CURRENT_WIKI_SCHEMA_VERSION,
        "needs_migration": (
            schema_version != CURRENT_WIKI_SCHEMA_VERSION
            or legacy_entity_count > 0
            or legacy_raw_count > 0
        ),
        "legacy_entity_count": legacy_entity_count,
        "legacy_raw_count": legacy_raw_count,
        "wiki_dir": wiki_dir,
        "total_songs": count_md_files(songs_dir),
        "total_artists": count_md_files(artists_dir),
        "total_genres": count_md_files(genres_dir),
        "total_albums": count_md_files(albums_dir),
        "last_ingested_at": last_ingested,
    }


def load_alias_index(wiki_dir: Optional[str] = None) -> Dict[str, List[str]]:
    """Load alias-index.json. Returns empty dict if not found."""
    wiki_dir = wiki_dir or settings.WIKI_DIR
    alias_path = os.path.join(wiki_dir, "alias-index.json")

    if not os.path.exists(alias_path):
        return {}

    with open(alias_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_alias_index(alias_index: Dict[str, List[str]], wiki_dir: Optional[str] = None) -> None:
    """Write alias_index dict to alias-index.json."""
    wiki_dir = wiki_dir or settings.WIKI_DIR
    alias_path = os.path.join(wiki_dir, "alias-index.json")

    with open(alias_path, "w", encoding="utf-8") as f:
        json.dump(alias_index, f, indent=2, ensure_ascii=False)
