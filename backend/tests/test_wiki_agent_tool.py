import json
import os
import sys

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


def test_convert_video_queues_background_job_without_writing_wiki(monkeypatch):
    ingest_called = False

    def unexpected_ingest(*args, **kwargs):
        nonlocal ingest_called
        ingest_called = True

    captured = {}

    def fake_create_download_job(*, user_id, items):
        captured.update({"user_id": user_id, "items": items})
        return {
            "id": "job-1",
            "user_id": user_id,
            "status": "queued",
            "items": items,
        }

    monkeypatch.setattr("services.wiki_ingest.ingest_song", unexpected_ingest)
    monkeypatch.setattr(
        "services.download_job_service.create_download_job",
        fake_create_download_job,
    )

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
    assert result["status"] == "queued"
    assert result["job_id"] == "job-1"
    assert captured["user_id"] == "local"
    assert captured["items"] == [{
        "bvid": "BV123",
        "url": "https://www.bilibili.com/video/BV123",
        "title": "测试歌曲",
        "artist": "测试歌手",
        "uploader": "测试上传者",
        "video_title": "原始视频标题",
    }]
