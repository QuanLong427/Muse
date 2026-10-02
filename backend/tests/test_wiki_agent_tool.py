import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import ai_agent
from services.ai_agent import _build_tools
from services.wiki_operations import run_wiki_ingest


def test_wiki_operations_are_not_global_agent_tools():
    tools = {tool.name for tool in _build_tools()}

    assert "wiki_ingest" not in tools
    assert "wiki_search" not in tools
    assert "activate_skill" in tools
    assert "execute_skill_script" in tools
    assert "load_skill_reference" in tools
    assert "bash" not in tools
    assert "read_file" not in tools


def test_execute_skill_script_runs_without_shell_and_blocks_traversal(tmp_path):
    tool = {item.name: item for item in _build_tools()}["execute_skill_script"]
    metadata = json.dumps({"bvid": "BV123", "title": "测试歌曲"}, ensure_ascii=False)

    result = json.loads(
        tool.invoke(
            {
                "skill_name": "llm-wiki",
                "script_name": "wiki_ops.py",
                "arguments": json.dumps(
                    [
                        "--wiki-dir",
                        str(tmp_path),
                        "ingest",
                        "--metadata-json",
                        metadata,
                    ],
                    ensure_ascii=False,
                ),
            }
        )
    )
    assert result["status"] == "completed"
    assert json.loads(result["stdout"])["writes_performed"] is False

    inferred = json.loads(
        tool.invoke(
            {
                "skill_name": "llm-wiki",
                "arguments": json.dumps(
                    [
                        "--wiki-dir",
                        str(tmp_path),
                        "--metadata-json",
                        metadata,
                        "--preview",
                    ],
                    ensure_ascii=False,
                ),
            }
        )
    )
    assert inferred["status"] == "completed"
    assert json.loads(inferred["stdout"])["writes_performed"] is False

    nested = json.loads(
        tool.invoke(
            {
                "skill_name": "llm-wiki",
                "script_name": "wiki_ops.py",
                "arguments": json.dumps(
                    [
                        json.dumps(
                            [
                                "--wiki-dir",
                                str(tmp_path),
                                "ingest",
                                "--metadata-json",
                                metadata,
                            ],
                            ensure_ascii=False,
                        )[:-1]
                    ],
                    ensure_ascii=False,
                ),
            }
        )
    )
    assert nested["status"] == "completed"
    assert json.loads(nested["stdout"])["writes_performed"] is False

    blocked = json.loads(
        tool.invoke(
            {
                "skill_name": "llm-wiki",
                "script_name": "../SKILL.md",
                "arguments": "[]",
            }
        )
    )
    assert blocked["error"] == "invalid_skill_script"


def test_wiki_ingest_plan_is_read_only(monkeypatch):
    called = False

    def unexpected_ingest(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr("services.wiki_ingest.ingest_song", unexpected_ingest)

    result = run_wiki_ingest({"bvid": "BV123", "title": "测试歌曲"}, apply=False)

    assert result["status"] == "planned"
    assert result["writes_performed"] is False
    assert called is False


def test_wiki_ingest_apply_uses_backend_service(monkeypatch):
    ingested = []

    monkeypatch.setattr(
        "services.wiki_manager.get_wiki_status",
        lambda wiki_dir=None: {"initialized": True},
    )
    monkeypatch.setattr(
        "services.wiki_ingest.ingest_song",
        lambda metadata, wiki_dir=None: ingested.append(metadata) or {"status": "created", "title": metadata["title"]},
    )

    result = run_wiki_ingest(
        {
            "bvid": "BV123",
            "title": "测试歌曲",
            "videoTitle": "原始视频标题",
        },
        apply=True,
    )

    assert result["status"] == "completed"
    assert result["writes_performed"] is True
    assert ingested[0]["video_title"] == "原始视频标题"


def test_wiki_ingest_rejects_missing_source_identity():
    try:
        run_wiki_ingest({}, apply=False)
    except ValueError as exc:
        assert "缺少来源标识字段" in str(exc)
    else:
        raise AssertionError("missing source identity should be rejected")


def test_convert_video_returns_ingest_ready_metadata_without_writing_wiki(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_agent.settings, "MUSIC_DIR", str(tmp_path))
    monkeypatch.setattr(ai_agent.settings, "WIKI_DIR", str(tmp_path / "LLM-Wiki"))
    monkeypatch.setattr("services.wiki_sync.DEFAULT_DB_PATH", tmp_path / "wiki-sync.db")
    monkeypatch.setattr(ai_agent.settings, "BILIBILI_COOKIES_FILE", "")
    monkeypatch.setattr(ai_agent.settings, "BILIBILI_DOWNLOAD_TIMEOUT_SECONDS", 300)

    ingest_called = False

    def unexpected_ingest(*args, **kwargs):
        nonlocal ingest_called
        ingest_called = True

    def fake_run(command, **kwargs):
        output_template = Path(command[command.index("--output") + 1])
        output_path = Path(str(output_template).replace("%(ext)s", "mp3"))
        output_path.write_bytes(b"test audio")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("services.wiki_ingest.ingest_song", unexpected_ingest)
    monkeypatch.setattr("services.bili_downloader.subprocess.run", fake_run)

    result = json.loads(
        {
            tool.name: tool
            for tool in _build_tools(
                selected_tracks=[
                    {
                        "bvid": "BV123",
                        "title": "测试歌曲",
                        "author": "测试歌手",
                        "url": "https://www.bilibili.com/video/BV123",
                    }
                ]
            )
        }["convert_video"].invoke({
            "urls": ["https://www.bilibili.com/video/BV123"],
            "song_meta_json": json.dumps([{
                "bvid": "BV123",
                "title": "测试歌曲",
                "artist": "测试歌手",
                "uploader": "测试上传者",
                "videoTitle": "原始视频标题",
            }]),
        })
    )

    assert result["success"] is True
    assert ingest_called is False
    assert result["files"][0] == {
        "original": "BV123.mp3",
        "renamed": "测试歌手-测试歌曲-BV123.mp3",
        "bvid": "BV123",
        "title": "测试歌曲",
        "artist": "测试歌手",
        "uploader": "测试上传者",
        "video_title": "原始视频标题",
        "url": "https://www.bilibili.com/video/BV123",
        "local_file_path": str(
            (
                next(
                    path
                    for path in tmp_path.iterdir()
                    if path.is_dir() and path.name.isdigit()
                )
                / "测试歌手-测试歌曲-BV123.mp3"
            ).resolve()
        ),
        "existing": False,
    }
