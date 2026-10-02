import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

from config import settings
from models import Track


def _is_year(s: str) -> bool:
    return bool(re.match(r"^\d{4}$", s)) and "1990" <= s <= "2030"


def _is_num(s: str) -> bool:
    return bool(re.match(r"^\d{1,2}$", s))


def parse_name(name: str) -> dict[str, str | None]:
    bvid = ""
    bvid_match = re.search(r"[-_ ]*(BV[A-Za-z0-9]+)$", name)
    if bvid_match:
        bvid = bvid_match.group(1)
        name = name[: -len(bvid_match.group(0))].rstrip("-_ ")

    parts = name.split("-")
    n = len(parts)

    if n >= 4:
        y, m, d = parts[n - 3], parts[n - 2], parts[n - 1]
        if _is_year(y) and _is_num(m) and _is_num(d):
            date = f"{y}-{m}-{d}"
            if n >= 5:
                return {
                    "title": "-".join(parts[: n - 4]).strip(),
                    "author": parts[n - 4].strip(),
                    "date": date,
                    "bvid": bvid or None,
                }
            return {
                "title": "-".join(parts[: n - 3]).strip(),
                "author": "",
                "date": date,
                "bvid": bvid or None,
            }

    # Canonical downloaded-file format: Artist-Title-BVID.  Only apply this
    # heuristic when a BVID is present so ordinary hyphenated titles stay intact.
    if bvid and "-" in name:
        author, title = name.split("-", 1)
        if author.strip() and title.strip():
            return {
                "title": title.strip("-_ "),
                "author": author.strip(),
                "date": "",
                "bvid": bvid,
            }

    return {"title": name, "author": "", "date": "", "bvid": bvid or None}


def scan_tracks(music_dir: str | None = None) -> list[Track]:
    music_dir = music_dir or settings.MUSIC_DIR
    tracks: list[Track] = []

    try:
        dirs = [
            d for d in os.scandir(music_dir)
            if d.is_dir()
        ]
    except FileNotFoundError:
        return tracks

    for entry in dirs:
        sub_dir = entry.name
        try:
            files = [
                f for f in os.scandir(entry.path)
                if f.is_file() and f.name.lower().endswith(".mp3")
            ]
        except Exception:
            continue

        for f in files:
            try:
                size = f.stat().st_size
            except Exception:
                size = 0

            base_name = f.name[:-4]  # remove .mp3
            parsed = parse_name(base_name)

            track = Track(
                id=f"{sub_dir}/{f.name}",
                title=parsed["title"] or "",
                author=parsed["author"] or "",
                date=parsed["date"] or "",
                filename=f.name,
                subDir=sub_dir,
                size=size,
                url=f'/api/tracks/{quote(sub_dir, safe="")}/{quote(f.name, safe="")}',
                bvid=parsed["bvid"],
            )
            tracks.append(track)

    return tracks


def find_track_by_bvid(bvid: str) -> Track | None:
    """Find a local track by its BV id."""
    for track in scan_tracks():
        if track.bvid == bvid:
            return track
    return None


def find_track_by_id(track_id: str) -> Track | None:
    """Resolve an exact local track identifier without fuzzy title matching."""
    return next((track for track in scan_tracks() if track.id == track_id), None)


def _normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def search_tracks(query: str, limit: int = 20) -> list[Track]:
    all_tracks = scan_tracks()
    if not query:
        return all_tracks[:limit]

    tokens = [
        token
        for token in (
            _normalize_search_text(part) for part in re.split(r"\s+", query.strip())
        )
        if token
    ]
    if not tokens:
        return all_tracks[:limit]

    ranked: list[tuple[int, str, Track]] = []
    for track in all_tracks:
        title = _normalize_search_text(track.title)
        author = _normalize_search_text(track.author)
        haystack = _normalize_search_text(
            f"{track.title} {track.author} {track.filename}"
        )
        if not all(token in haystack for token in tokens):
            continue
        score = sum(
            4 if token == title else 3 if token in title else 2 if token in author else 1
            for token in tokens
        )
        ranked.append((-score, track.id, track))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [track for _, _, track in ranked[:limit]]


def scan_subdir(sub_dir: str) -> list[Track]:
    music_dir = settings.MUSIC_DIR
    dir_path = Path(music_dir) / sub_dir

    # Path traversal protection
    try:
        dir_path.resolve().relative_to(Path(music_dir).resolve())
    except ValueError:
        return []

    tracks: list[Track] = []
    try:
        files = [
            f for f in os.scandir(dir_path)
            if f.is_file() and f.name.lower().endswith(".mp3")
        ]
    except FileNotFoundError:
        return tracks

    for f in files:
        try:
            size = f.stat().st_size
        except Exception:
            size = 0

        base_name = f.name[:-4]
        parsed = parse_name(base_name)

        track = Track(
            id=f"{sub_dir}/{f.name}",
            title=parsed["title"] or "",
            author=parsed["author"] or "",
            date=parsed["date"] or "",
            filename=f.name,
            subDir=sub_dir,
            size=size,
            url=f'/api/tracks/{quote(sub_dir, safe="")}/{quote(f.name, safe="")}',
            bvid=parsed["bvid"],
        )
        tracks.append(track)

    return tracks


def resolve_music_path(relative_path: str) -> str | None:
    music_dir = Path(settings.MUSIC_DIR)
    full = (music_dir / relative_path).resolve()
    if not str(full).startswith(str(music_dir.resolve())):
        return None
    if not full.is_file():
        return None
    return str(full)
