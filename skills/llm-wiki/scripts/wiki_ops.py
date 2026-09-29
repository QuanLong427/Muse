#!/usr/bin/env python3
"""Deterministic entry point for Musicer's LLM-Wiki status, audit and ingest."""

from __future__ import annotations

import argparse
import json
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
from services.wiki_operations import run_wiki_ingest  # noqa: E402
from services.wiki_manager import audit_wiki_quality, get_wiki_status  # noqa: E402


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _load_metadata(source: str) -> dict[str, Any]:
    content = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("metadata must be one JSON object")
    if not any(data.get(key) for key in ("title", "video_title", "bvid", "url", "local_file_path")):
        raise ValueError("metadata needs at least one source identity field")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Operate the Musicer LLM-Wiki through backend services")
    parser.add_argument("--wiki-dir", help="Override WIKI_DIR for an isolated or test Wiki")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show Schema and migration status")
    audit_parser = subparsers.add_parser("audit", help="Run a read-only provenance and graph audit")
    audit_parser.add_argument("--max-issues", type=int, default=500)

    ingest_parser = subparsers.add_parser("ingest", help="Validate an ingest request; use --apply to write")
    ingest_parser.add_argument("--metadata", required=True, help="UTF-8 JSON file, or - for stdin")
    ingest_parser.add_argument("--apply", action="store_true", help="Perform the LLM call and Wiki write")

    args = parser.parse_args()
    wiki_dir = args.wiki_dir or settings.WIKI_DIR

    if args.command == "status":
        _print_json(get_wiki_status(wiki_dir))
        return 0
    if args.command == "audit":
        if args.max_issues < 1:
            parser.error("--max-issues must be positive")
        _print_json(audit_wiki_quality(wiki_dir, max_issues=args.max_issues))
        return 0

    metadata = _load_metadata(args.metadata)
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
