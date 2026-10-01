#!/usr/bin/env python3
"""Run an isolated live-model acceptance check for Musicer's llm-wiki Skill."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any


def _find_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "backend" / "services" / "ai_agent.py").is_file():
            return parent
    raise RuntimeError("Musicer project root was not found")


PROJECT_ROOT = _find_project_root()
BACKEND_DIR = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from config import settings  # noqa: E402
from services.ai_agent import _build_agent, _build_system_prompt, _build_tools  # noqa: E402
from services.music_manager import parse_name  # noqa: E402
from services.skill_loader import discover_skills, load_skill  # noqa: E402
from services.wiki_manager import (  # noqa: E402
    audit_wiki_quality,
    get_wiki_status,
    init_wiki,
    reset_wiki,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


async def _run_agent_turn(prompt: str) -> dict[str, Any]:
    agent = _build_agent(
        _build_system_prompt("验收", "llm-wiki-live-check"),
        scenario="验收",
        player_state={"available": False, "reason": "隔离验收没有浏览器播放器"},
        user_id="llm-wiki-live-check",
        session_id="llm-wiki-live-check",
    )
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=prompt)]},
        config={"recursion_limit": 50},
    )
    calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    final_text = ""
    for message in result.get("messages", []):
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                calls.append(
                    {
                        "name": call.get("name", ""),
                        "args": _jsonable(call.get("args", {})),
                    }
                )
            if not message.tool_calls and isinstance(message.content, str) and message.content:
                final_text = message.content
        elif isinstance(message, ToolMessage):
            tool_results.append(
                {
                    "name": message.name or "",
                    "content": str(message.content)[:3000],
                }
            )
    return {"calls": calls, "tool_results": tool_results, "final_text": final_text}


def _call_names(turn: dict[str, Any]) -> list[str]:
    return [call["name"] for call in turn["calls"]]


def _wiki_script_calls(*turns: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for turn in turns:
        for call in turn["calls"]:
            args = call.get("args", {})
            if (
                call["name"] == "execute_skill_script"
                and isinstance(args, dict)
                and args.get("skill_name") == "llm-wiki"
                and args.get("script_name", "") in {"", "wiki_ops.py"}
            ):
                calls.append(call)
    return calls


def _calls_for(action: str, *turns: dict[str, Any]) -> list[dict[str, Any]]:
    matched = []
    for call in _wiki_script_calls(*turns):
        arguments = call.get("args", {}).get("arguments", [])
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = []
        if (
            isinstance(arguments, list)
            and len(arguments) == 1
            and isinstance(arguments[0], str)
        ):
            nested = None
            candidates = [arguments[0]]
            if arguments[0].lstrip().startswith("[") and not arguments[0].rstrip().endswith("]"):
                candidates.append(arguments[0] + "]")
            for candidate in candidates:
                try:
                    nested = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue
            if isinstance(nested, list):
                arguments = nested
        inferred_ingest = action == "ingest" and isinstance(arguments, list) and any(
            flag in arguments for flag in ("--metadata", "--metadata-json")
        )
        if isinstance(arguments, list) and (action in arguments or inferred_ingest):
            matched.append(call)
    return matched


def _uses_bash_for_wiki_script(*turns: dict[str, Any]) -> bool:
    for turn in turns:
        for call in turn["calls"]:
            args = call.get("args", {})
            command = args.get("command", "") if isinstance(args, dict) else ""
            if call["name"] == "bash" and "wiki_ops.py" in str(command):
                return True
    return False


def _skill_script_results_succeed(*turns: dict[str, Any]) -> bool:
    results = [
        result["content"]
        for turn in turns
        for result in turn["tool_results"]
        if result["name"] == "execute_skill_script"
    ]
    if not results:
        return False
    for content in results:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return False
        if parsed.get("status") != "completed" or parsed.get("exit_code") != 0:
            return False
    return True


async def _verify(source_audio: Path, case_root: Path) -> dict[str, Any]:
    music_dir = case_root / "music"
    album_dir = music_dir / "test-album"
    wiki_dir = case_root / "wiki"
    manifest_dir = case_root / "manifests"
    album_dir.mkdir(parents=True, exist_ok=True)
    test_audio = album_dir / source_audio.name
    shutil.copy2(source_audio, test_audio)

    original_name = test_audio.name
    original_hash = _sha256(test_audio)
    original_size = test_audio.stat().st_size

    settings.MUSIC_DIR = str(music_dir)
    settings.WIKI_DIR = str(wiki_dir)
    os.environ["MUSIC_DIR"] = str(music_dir)
    os.environ["WIKI_DIR"] = str(wiki_dir)
    init_wiki(str(wiki_dir))

    parsed = parse_name(test_audio.stem)
    metadata = {
        "title": parsed.get("title") or test_audio.stem,
        "artist": parsed.get("author") or "",
        "video_title": test_audio.stem,
        "bvid": parsed.get("bvid") or "",
        "local_file_path": str(test_audio),
        "url": (
            f"https://www.bilibili.com/video/{parsed['bvid']}"
            if parsed.get("bvid")
            else ""
        ),
        "description": "LLM-Wiki 隔离 Live 验收素材。",
        "external_sources": [],
    }
    metadata_json = json.dumps(metadata, ensure_ascii=False)

    discovered = {item["name"] for item in discover_skills()}
    full_skill, skill_body = load_skill("llm-wiki")
    prompt = _build_system_prompt("验收", "llm-wiki-live-check")

    non_wiki_turn = await _run_agent_turn(
        "播放器不可用时，请说明为什么不能播放下一首。不要查询或修改音乐知识库。"
    )
    audit_turn = await _run_agent_turn(
        "请检查当前音乐知识库的 Schema、质量以及是否可以提供已验证回答。"
    )
    preview_turn = await _run_agent_turn(
        "请检查下面这条真实来源元数据能否进入音乐知识库，只做预览，禁止写入。"
        f"\n元数据：{metadata_json}"
    )
    status_after_preview = get_wiki_status(str(wiki_dir))
    apply_turn = await _run_agent_turn(
        "我明确确认并授权：将下面这条真实来源元数据正式写入音乐知识库。"
        "必须遵守 llm-wiki 的证据和写入流程。"
        f"\n元数据：{metadata_json}"
    )

    preview_calls = _calls_for("ingest", preview_turn)
    apply_calls = _calls_for("ingest", apply_turn)
    def parsed_arguments(call: dict[str, Any]) -> list[str]:
        value = call.get("args", {}).get("arguments", "[]")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return []
        if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
            nested = None
            candidates = [value[0]]
            if value[0].lstrip().startswith("[") and not value[0].rstrip().endswith("]"):
                candidates.append(value[0] + "]")
            for candidate in candidates:
                try:
                    nested = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue
            if isinstance(nested, list):
                value = nested
        return value if isinstance(value, list) else []

    preview_without_apply = [call for call in preview_calls if "--apply" not in parsed_arguments(call)]
    apply_with_apply = [call for call in apply_calls if "--apply" in parsed_arguments(call)]
    status_after_apply = get_wiki_status(str(wiki_dir))
    audit_after_apply = audit_wiki_quality(str(wiki_dir))

    query_turn = await _run_agent_turn(
        f"请使用 llm-wiki 查询音乐知识库中的《{metadata['title']}》，说明表演者以及信息的核验状态；"
        "没有 verified 证据时必须明确说明。"
    )

    query_text = query_turn["final_text"]
    query_tool_text = "\n".join(result["content"] for result in query_turn["tool_results"])
    qualifying_phrases = ("待核验", "尚未独立核验", "needs_review", "证据不足", "未验证")

    reset_result = reset_wiki(
        wiki_dir=str(wiki_dir),
        music_dir=str(music_dir),
        manifest_dir=str(manifest_dir),
    )
    status_after_reset = get_wiki_status(str(wiki_dir))
    manifest_path = next(manifest_dir.glob("wiki-reset-*.json"), None)

    checks = {
        "skill_discovered": "llm-wiki" in discovered,
        "skill_loaded": bool(full_skill and skill_body and "name: llm-wiki" in full_skill),
        "system_prompt_metadata_only": "llm-wiki" in prompt and "## Invariants" not in prompt,
        "global_wiki_tools_removed": not ({"wiki_ingest", "wiki_search"} & {tool.name for tool in _build_tools()}),
        "skill_script_executor_available": "execute_skill_script" in {tool.name for tool in _build_tools()},
        "non_wiki_avoids_skill": "activate_skill" not in _call_names(non_wiki_turn),
        "audit_activates_skill": "activate_skill" in _call_names(audit_turn),
        "audit_uses_skill_script": bool(_calls_for("audit", audit_turn)),
        "preview_activates_skill": "activate_skill" in _call_names(preview_turn),
        "apply_activates_skill": "activate_skill" in _call_names(apply_turn),
        "preview_uses_script_without_write": len(preview_without_apply) == 1 and status_after_preview["total_songs"] == 0,
        "explicit_apply_uses_script_once": len(apply_with_apply) == 1,
        "ingest_created_song": status_after_apply["total_songs"] >= 1,
        "schema_v3": status_after_apply["schema_version"] == "3.0",
        "audit_structurally_valid": audit_after_apply.get("structurally_valid") is True,
        "audit_not_falsely_verified": audit_after_apply.get("ready_for_verified_answers") is False,
        "query_activates_skill": "activate_skill" in _call_names(query_turn),
        "query_uses_search_script": bool(_calls_for("search", query_turn)),
        "query_script_preserves_identifier": not metadata["bvid"] or metadata["bvid"] in query_tool_text,
        "query_qualifies_unverified_claims": any(phrase in query_text for phrase in qualifying_phrases),
        "wiki_script_avoids_bash": not _uses_bash_for_wiki_script(
            audit_turn, preview_turn, apply_turn, query_turn
        ),
        "skill_script_calls_succeed": _skill_script_results_succeed(
            audit_turn, preview_turn, apply_turn, query_turn
        ),
        "audio_name_unchanged": test_audio.name == original_name,
        "audio_size_unchanged": test_audio.stat().st_size == original_size,
        "audio_hash_unchanged": _sha256(test_audio) == original_hash,
        "reset_succeeded": reset_result.get("status") == "reset",
        "reset_preserved_audio": test_audio.is_file() and _sha256(test_audio) == original_hash,
        "recovery_manifest_exists": manifest_path is not None and manifest_path.is_file(),
        "post_reset_empty_v3": (
            status_after_reset.get("schema_version") == "3.0"
            and status_after_reset.get("total_songs") == 0
            and not status_after_reset.get("needs_migration")
        ),
    }
    return {
        "case_root": str(case_root),
        "model": settings.MODEL_NAME,
        "source_audio": source_audio.name,
        "metadata": metadata,
        "checks": checks,
        "passed": all(checks.values()),
        "turns": {
            "non_wiki": non_wiki_turn,
            "audit": audit_turn,
            "preview": preview_turn,
            "apply": apply_turn,
            "query": query_turn,
        },
        "status_after_preview": status_after_preview,
        "status_after_apply": status_after_apply,
        "audit_after_apply": audit_after_apply,
        "reset": reset_result,
        "status_after_reset": status_after_reset,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-audio", help="MP3 source copied into the isolated case")
    parser.add_argument("--case-root", help="Directory for isolated Wiki, audio copy and report")
    args = parser.parse_args()

    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required for live verification")
    source_audio = Path(args.source_audio).resolve() if args.source_audio else next(
        Path(settings.MUSIC_DIR).rglob("*.mp3"), None
    )
    if source_audio is None or not source_audio.is_file():
        raise RuntimeError("No MP3 source is available for live verification")

    case_root = (
        Path(args.case_root).resolve()
        if args.case_root
        else PROJECT_ROOT / "backend" / ".pytest-tmp" / f"llm-wiki-live-{uuid.uuid4().hex}"
    )
    case_root.mkdir(parents=True, exist_ok=False)
    report = asyncio.run(_verify(source_audio, case_root))
    report_path = case_root / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "passed": report["passed"],
        "model": report["model"],
        "case_root": report["case_root"],
        "report": str(report_path),
        "checks": report["checks"],
        "tool_sequences": {
            name: _call_names(turn) for name, turn in report["turns"].items()
        },
    }, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
