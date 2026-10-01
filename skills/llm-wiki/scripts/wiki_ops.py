#!/usr/bin/env python3
"""Deterministic entry point for all Musicer LLM-Wiki operations."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "backend" / "services" / "wiki_manager.py").is_file():
            return parent
    raise RuntimeError("Musicer project root was not found from the Skill directory")


PROJECT_ROOT = _find_project_root()
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from config import settings  # noqa: E402
from services.wiki_operations import run_wiki_ingest, run_wiki_search  # noqa: E402
from services.wiki_manager import (  # noqa: E402
    audit_wiki_quality,
    build_wiki_recovery_manifest,
    get_wiki_status,
    reset_wiki,
)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _load_metadata(source: str | None, inline_json: str | None) -> Any:
    if inline_json is not None:
        content = inline_json
    elif source == "-":
        content = sys.stdin.read()
    elif source:
        content = Path(source).read_text(encoding="utf-8")
    else:
        raise ValueError("metadata source is required")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Qwen's OpenAI-compatible tool calls may preserve a Windows path with
        # single backslashes inside the nested metadata JSON. Repair only
        # drive-letter path substrings; all other malformed JSON stays invalid.
        repaired = re.sub(
            r"([A-Za-z]:)([^\"\r\n]*)",
            lambda match: match.group(1)
            + re.sub(r"(?<!\\)\\(?!\\)", r"\\\\", match.group(2)),
            content,
        )
        if repaired == content:
            raise
        data = json.loads(repaired)
    if not isinstance(data, (dict, list)):
        raise ValueError("metadata must be one JSON object or a non-empty object array")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Operate the Musicer LLM-Wiki through backend services")
    parser.add_argument("--wiki-dir", help="Override WIKI_DIR for an isolated or test Wiki")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show Schema and migration status")
    audit_parser = subparsers.add_parser("audit", help="Run a read-only provenance and graph audit")
    audit_parser.add_argument("--max-issues", type=int, default=500)

    search_parser = subparsers.add_parser("search", help="Search entities and return exact evidence")
    search_parser.add_argument("--query", required=True, help="Song, artist, album, genre or question")
    search_parser.add_argument("--limit", type=int, default=8, help="Maximum results (1-20)")

    reset_parser = subparsers.add_parser(
        "reset", help="Preview a safe Wiki reset; use --apply to perform it"
    )
    reset_parser.add_argument(
        "--apply", action="store_true", help="Write a recovery manifest and reset generated Wiki data"
    )
    reset_parser.add_argument("--music-dir", help="Override MUSIC_DIR for manifest generation")
    reset_parser.add_argument("--manifest-dir", help="Override the recovery manifest directory")

    ingest_parser = subparsers.add_parser("ingest", help="Validate an ingest request; use --apply to write")
    metadata_group = ingest_parser.add_mutually_exclusive_group(required=True)
    metadata_group.add_argument("--metadata", help="UTF-8 JSON file, or - for stdin")
    metadata_group.add_argument("--metadata-json", help="Inline JSON object or array")
    ingest_mode = ingest_parser.add_mutually_exclusive_group()
    ingest_mode.add_argument("--apply", action="store_true", help="Perform the LLM call and Wiki write")
    ingest_mode.add_argument(
        "--preview",
        action="store_true",
        help="Explicit read-only preview (the default when --apply is omitted)",
    )

    argv = sys.argv[1:]
    commands = {"status", "audit", "search", "reset", "ingest"}
    if not any(argument in commands for argument in argv) and any(
        flag in argv for flag in ("--metadata", "--metadata-json")
    ):
        insert_at = 2 if argv[:1] == ["--wiki-dir"] and len(argv) >= 2 else 0
        argv.insert(insert_at, "ingest")
    args = parser.parse_args(argv)
    wiki_dir = args.wiki_dir or settings.WIKI_DIR

    if args.command == "status":
        _print_json(get_wiki_status(wiki_dir))
        return 0
    if args.command == "audit":
        if args.max_issues < 1:
            parser.error("--max-issues must be positive")
        _print_json(audit_wiki_quality(wiki_dir, max_issues=args.max_issues))
        return 0
    if args.command == "search":
        _print_json(run_wiki_search(args.query, wiki_dir=wiki_dir, limit=args.limit))
        return 0
    if args.command == "reset":
        music_dir = args.music_dir or settings.MUSIC_DIR
        if not args.apply:
            manifest = build_wiki_recovery_manifest(wiki_dir, music_dir)
            _print_json(
                {
                    "status": "planned",
                    "writes_performed": False,
                    "preserved_local_tracks": manifest["track_count"],
                    "previous_wiki_sources": manifest["previous_wiki_source_count"],
                    "apply_requires_explicit_user_intent": True,
                }
            )
            return 0
        _print_json(
            reset_wiki(
                wiki_dir=wiki_dir,
                music_dir=music_dir,
                manifest_dir=args.manifest_dir,
            )
        )
        return 0

    metadata = _load_metadata(args.metadata, args.metadata_json)
    if args.apply and not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required for --apply")
    _print_json(run_wiki_ingest(metadata, apply=args.apply, wiki_dir=wiki_dir))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
