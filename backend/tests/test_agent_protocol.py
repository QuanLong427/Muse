import json
import os
import sys

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.agent_protocol import (
    ProtocolViolation,
    safe_protocol_response,
    validate_final_response,
)


TOOLS = {
    "local_search",
    "bili_search",
    "convert_video",
    "control_player",
    "manage_playback_session",
    "set_playback_mode",
    "record_track_feedback",
    "recommend_next",
}


def test_rejects_tool_name_written_as_shell_text():
    violation = validate_final_response(
        '让我搜索一下：\n```bash\nbili_search --keyword "七里香"\n```',
        [AIMessage(content="draft")],
        TOOLS,
    )

    assert violation is not None
    assert violation.code == "textual_tool_call"


def test_rejects_unfinished_action_promise():
    violation = validate_final_response(
        "这首歌可能有多个版本，让我先搜索",
        [],
        TOOLS,
    )

    assert violation is not None
    assert violation.code == "unfinished_action"


def test_rejects_unfenced_textual_tool_call():
    violation = validate_final_response(
        'bili_search(keyword="七里香")',
        [],
        TOOLS,
    )

    assert violation is not None
    assert violation.code == "textual_tool_call"


def test_rejects_model_fabricated_legacy_track_cards():
    violation = validate_final_response(
        '推荐如下：\n```tracks\n[{"id":"local/song.mp3","title":"Song"}]\n```',
        [],
        TOOLS,
    )

    assert violation is not None
    assert violation.code == "fabricated_track_cards"


def test_rejects_success_claim_without_tool_observation():
    violation = validate_final_response("已经播放《最长的电影》。", [], TOOLS)

    assert violation is not None
    assert violation.code == "unobserved_action_claim"


def test_rejects_playlist_creation_claim_without_tool_observation():
    violation = validate_final_response(
        "已经创建歌单“夜跑”。", [], TOOLS | {"create_music_playlist"}
    )

    assert violation is not None
    assert violation.code == "unobserved_action_claim"


def test_rejects_definite_playback_claim_without_client_ack():
    violation = validate_final_response(
        "已播放《最长的电影》。",
        [
            ToolMessage(
                content='{"status":"dispatched","client_action":{"target":"player","action":"play_track"}}',
                tool_call_id="call-1",
                name="play_track",
            )
        ],
        TOOLS | {"play_track"},
    )

    assert violation is not None
    assert violation.code == "unconfirmed_client_action"


def test_allows_definite_playback_claim_after_matching_client_ack():
    action_id = "action-1"
    tool_message = ToolMessage(
        content=json.dumps(
            {
                "status": "dispatched",
                "client_action": {
                    "target": "player",
                    "action": "play_track",
                    "action_id": action_id,
                },
            }
        ),
        tool_call_id="call-1",
        name="play_track",
    )

    violation = validate_final_response(
        "已播放《最长的电影》。",
        [tool_message],
        TOOLS | {"play_track"},
        client_action_results={
            action_id: {"status": "succeeded", "result": {"observed": "playing"}}
        },
    )

    assert violation is None


def test_rejects_success_claim_after_failed_client_ack():
    action_id = "action-1"
    tool_message = ToolMessage(
        content=json.dumps(
            {
                "status": "dispatched",
                "client_action": {
                    "target": "player",
                    "action": "play_track",
                    "action_id": action_id,
                },
            }
        ),
        tool_call_id="call-1",
        name="play_track",
    )

    violation = validate_final_response(
        "已播放《最长的电影》。",
        [tool_message],
        TOOLS | {"play_track"},
        client_action_results={
            action_id: {"status": "failed", "result": {"error": "audio error"}}
        },
    )

    assert violation is not None
    assert violation.code == "client_action_failed"


def test_allows_dispatch_wording_with_real_tool_message():
    violation = validate_final_response(
        "已向播放器发送《最长的电影》的播放指令。",
        [
            ToolMessage(
                content='{"status":"dispatched","client_action":{"target":"player","action":"play_track"}}',
                tool_call_id="call-1",
                name="play_track",
            )
        ],
        TOOLS | {"play_track"},
    )

    assert violation is None


def test_allows_ordinary_conversation():
    assert validate_final_response("当然可以，你想听什么风格？", [], TOOLS) is None


def test_allows_capability_description_about_downloaded_tracks():
    text = "我可以播放已下载的本地歌曲，也可以暂停、切换下一首。"

    assert (
        validate_final_response(
            text,
            [HumanMessage(content="你可以做哪些事情")],
            TOOLS,
        )
        is None
    )


def test_still_rejects_actual_download_success_claim_without_tool():
    violation = validate_final_response("《七里香》已经下载到本地。", [], TOOLS)

    assert violation is not None
    assert violation.code == "unobserved_action_claim"


def test_rejects_download_success_claim_when_job_is_only_queued():
    messages = [
        HumanMessage(content="下载七里香"),
        ToolMessage(
            name="convert_video",
            tool_call_id="download-1",
            content='{"success":true,"status":"queued","job_id":"job-123"}',
        ),
    ]

    violation = validate_final_response("《七里香》已经下载完成。", messages, TOOLS)

    assert violation is not None
    assert violation.code == "download_still_queued"
    assert "后台队列" in safe_protocol_response(violation, messages)


def test_still_rejects_actual_conversion_success_claim_without_tool():
    violation = validate_final_response("《七里香》转换已完成。", [], TOOLS)

    assert violation is not None
    assert violation.code == "unobserved_action_claim"


def test_rejects_mode_change_claim_without_tool_observation():
    violation = validate_final_response("已切换为随机播放。", [], TOOLS)

    assert violation is not None
    assert violation.code == "unobserved_action_claim"


def test_rejects_feedback_claim_without_tool_observation():
    violation = validate_final_response("已记录你不喜欢这个版本。", [], TOOLS)

    assert violation is not None
    assert violation.code == "unobserved_action_claim"


def test_allows_feedback_claim_with_real_tool_observation():
    violation = validate_final_response(
        "已记录你不喜欢这个版本。",
        [
            ToolMessage(
                content='{"status":"recorded"}',
                tool_call_id="call-feedback",
                name="record_track_feedback",
            )
        ],
        TOOLS,
    )

    assert violation is None


def test_rejects_unconfirmed_recommendation_append_claim():
    violation = validate_final_response(
        "已将推荐歌曲加入接下来播放。",
        [
            ToolMessage(
                content='{"status":"dispatched","client_action":{"target":"player","action":"add_tracks"}}',
                tool_call_id="call-radio",
                name="recommend_next",
            )
        ],
        TOOLS,
    )

    assert violation is not None
    assert violation.code == "unconfirmed_client_action"


def test_safe_recovery_uses_presented_track_evidence():
    response = safe_protocol_response(
        ProtocolViolation("fabricated_track_cards", "invalid legacy block"),
        [
            ToolMessage(
                content=json.dumps(
                    {
                        "status": "presented",
                        "tracks": [
                            {"track_id": "one", "title": "一路向北"},
                            {"track_id": "two", "title": "最长的电影"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                tool_call_id="call-present",
                name="present_tracks",
            )
        ],
    )

    assert "2 个可验证的歌曲结果" in response
    assert "歌曲卡片" in response


def test_rejects_undisclosed_recommendation_source_fallback():
    messages = [
        ToolMessage(
            content=json.dumps(
                {
                    "status": "partial",
                    "source_plan": {
                        "planned": {"local": 2, "cloud": 2},
                        "actual": {"local": 2, "cloud": 0},
                    },
                    "user_notice": "在线候选不足，已使用本地歌曲补足；本地 2 首、在线 0 首。",
                },
                ensure_ascii=False,
            ),
            tool_call_id="call-recommend",
            name="recommend_music",
        )
    ]

    violation = validate_final_response("为你找到了两首本地歌曲。", messages, TOOLS)

    assert violation is not None
    assert violation.code == "undisclosed_recommendation_fallback"
    assert validate_final_response(
        "在线候选不足，因此这次只找到两首本地歌曲。", messages, TOOLS
    ) is None


def test_safe_recovery_includes_recommendation_fallback_notice():
    response = safe_protocol_response(
        ProtocolViolation("undisclosed_recommendation_fallback", "missing notice"),
        [
            ToolMessage(
                content=json.dumps(
                    {
                        "status": "partial",
                        "user_notice": "B站搜索暂时不可用，已将推荐动态调整为本地 2 首。",
                    },
                    ensure_ascii=False,
                ),
                tool_call_id="call-recommend",
                name="recommend_music",
            ),
            ToolMessage(
                content=json.dumps(
                    {
                        "status": "presented",
                        "tracks": [{"track_id": "one", "title": "一路向北"}],
                    },
                    ensure_ascii=False,
                ),
                tool_call_id="call-present",
                name="present_tracks",
            ),
        ],
    )

    assert "B站搜索暂时不可用" in response
    assert "1 个可验证的歌曲结果" in response
