import json
import subprocess
import sys
from pathlib import Path

from services.wiki_manager import init_wiki
from services.wiki_operations import run_wiki_search


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "skills" / "llm-wiki" / "scripts" / "wiki_ops.py"


def _write_song(wiki_dir: Path) -> None:
    song = wiki_dir / "wiki" / "entities" / "songs" / "Yellow.md"
    song.parent.mkdir(parents=True, exist_ok=True)
    song.write_text(
        """---
schema_version: "3.0"
entity_type: song
verification_status: needs_review
confidence: 0.9
sources: [BV1VR4y1Y7QS]
---

# Yellow

## Overview
来源标题将本条记录关联到 [[Coldplay]]。

## Evidence
- `raw.original_title`: Coldplay-Yellow--BV1VR4y1Y7QS

## Uncertainties
- 表演者尚未独立核验
""",
        encoding="utf-8",
    )
    (wiki_dir / "index.md").write_text(
        "# Music Knowledge Base Index\n\n## Songs\n- [[Yellow]]\n",
        encoding="utf-8",
    )
    (wiki_dir / "alias-index.json").write_text(
        json.dumps({"Coldplay": ["酷玩乐队"]}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_wiki_search_returns_exact_evidence_and_identifier(tmp_path):
    init_wiki(str(tmp_path))
    _write_song(tmp_path)

    result = run_wiki_search("请查询酷玩乐队的 Yellow", wiki_dir=str(tmp_path))

    assert result["status"] == "ok"
    assert result["results"][0]["name"] == "Yellow"
    assert result["results"][0]["verification_status"] == "needs_review"
    assert result["results"][0]["exact_identifiers"] == ["BV1VR4y1Y7QS"]
    assert "BV1VR4y1Y7QS" in result["results"][0]["evidence"]


def test_skill_script_search_and_inline_ingest_preview(tmp_path):
    init_wiki(str(tmp_path))
    _write_song(tmp_path)

    search = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--wiki-dir",
            str(tmp_path),
            "search",
            "--query",
            "Yellow",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert search.returncode == 0, search.stderr
    search_result = json.loads(search.stdout)
    assert search_result["results"][0]["exact_identifiers"] == ["BV1VR4y1Y7QS"]

    preview = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--wiki-dir",
            str(tmp_path),
            "ingest",
            "--metadata-json",
            json.dumps({"bvid": "BV123", "title": "测试歌曲"}, ensure_ascii=False),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert preview.returncode == 0, preview.stderr
    preview_result = json.loads(preview.stdout)
    assert preview_result["status"] == "planned"
    assert preview_result["writes_performed"] is False

    qwen_style_metadata = (
        r'{"bvid":"BV123","title":"测试歌曲",'
        r'"local_file_path":"E:\projects\Musicer\测试歌曲.mp3"}'
    )
    repaired_preview = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--wiki-dir",
            str(tmp_path),
            "ingest",
            "--metadata-json",
            qwen_style_metadata,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert repaired_preview.returncode == 0, repaired_preview.stderr
    repaired_result = json.loads(repaired_preview.stdout)
    assert repaired_result["sources"][0]["local_file_path"] == r"E:\projects\Musicer\测试歌曲.mp3"
