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
    "manage_playlist_draft",
    "local_search",
    "bili_search",
    "convert_video",
    "control_player",
    "manage_playback_session",
    "set_playback_mode",
    "record_track_feedback",
    "recommend_next",
    "create_smart_playlist",
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


def test_smart_playlist_preview_cannot_be_claimed_as_saved_playlist():
    preview = ToolMessage(
        content=json.dumps(
            {"status": "ok", "batch_id": "smart-1", "tracks": [{"id": "one"}]},
            ensure_ascii=False,
        ),
        tool_call_id="call-smart",
        name="create_smart_playlist",
    )

    violation = validate_final_response("已经创建了歌单。", [preview], TOOLS)

    assert violation is not None
    assert violation.code == "smart_playlist_not_saved"


def test_indirect_draft_edit_claim_cannot_hide_a_failed_tool_call():
    invalid = ToolMessage(content='{"status":"invalid","error":"bad arguments"}',
        tool_call_id="edit", name="manage_playlist_draft")
    read = ToolMessage(content='{"status":"draft","action":"read","draft":{"id":"d","revision":0}}',
        tool_call_id="read", name="manage_playlist_draft")
    text = "抱歉，系统无法直接删除。根据您的要求，我已将第三首《歌曲》从草稿中移除，其他歌曲保留。"
    violation = validate_final_response(text, [invalid, read], TOOLS)
    assert violation.code == "draft_edit_unconfirmed"
    assert validate_final_response(text, [], TOOLS).code == "draft_edit_unconfirmed"
    old = ToolMessage(content='{"status":"draft","draft":{"id":"d","status":"draft","revision":0}}',
        tool_call_id="old", name="create_smart_playlist")
    recovery = safe_protocol_response(violation, [old, invalid, read])
    assert "未得到成功回执" in recovery
    assert "已更新" not in recovery


def test_smart_playlist_saved_result_allows_created_claim():
    saved = ToolMessage(
        content=json.dumps(
            {
                "status": "created",
                "batch_id": "smart-1",
                "playlist": {"id": "playlist-1", "name": "夜跑"},
            },
            ensure_ascii=False,
        ),
        tool_call_id="call-smart",
        name="create_smart_playlist",
    )

    assert validate_final_response("已经创建了歌单。", [saved], TOOLS) is None


def test_smart_append_claims_update_not_creation_or_completed_downloads():
    saved = ToolMessage(content=json.dumps({"status": "queued", "target_playlist_id": "p",
        "playlist": {"id": "p", "name": "歌单一", "added_count": 2},
        "job": {"id": "j", "status": "queued"}}), tool_call_id="smart", name="create_smart_playlist")
    assert validate_final_response("已经加入歌单。", [saved], TOOLS) is None
    violation = validate_final_response("已经创建了歌单。", [saved], TOOLS)
    assert violation.code == "smart_playlist_append_not_create"
    text = safe_protocol_response(violation, [saved])
    assert "已更新" in text
    assert "2 首本地歌曲" in text
    assert "后台下载" in text
    violation = validate_final_response('已成功为"歌单一"添加10首歌，其中8首正在下载。', [saved], TOOLS)
    assert violation.code == "playlist_addition_pending"


def test_smart_playlist_queued_download_cannot_be_claimed_complete():
    queued = ToolMessage(content=json.dumps({"status": "queued", "playlist": {"id": "p", "name": "音乐"},
        "job": {"id": "j", "status": "queued"}}), tool_call_id="smart", name="create_smart_playlist")
    violation = validate_final_response("下载成功。", [queued], TOOLS)
    assert violation.code == "download_still_queued"


def test_smart_playlist_needing_download_cannot_be_claimed_playing():
    pending = ToolMessage(content=json.dumps({"status": "needs_download", "result_count": 2}),
        tool_call_id="smart", name="create_smart_playlist")
    violation = validate_final_response("已经开始播放。", [pending], TOOLS)
    assert violation.code == "smart_playlist_needs_download"


def test_failed_draft_edit_cannot_claim_removed_song():
    failure = ToolMessage(content="Error invoking tool: item_ids should be a valid list", tool_call_id="edit", name="manage_playlist_draft")
    violation = validate_final_response("已移除草稿中的第三首，其余不变。", [failure], TOOLS)
    assert violation.code == "draft_edit_unconfirmed"
    assert "未得到成功回执" in safe_protocol_response(violation, [failure])


def test_successful_draft_edit_requires_revision_receipt():
    success = ToolMessage(content=json.dumps({"status": "draft", "action": "remove", "previous_revision": 0,
        "draft": {"id": "d", "status": "draft", "revision": 1, "items": []}}), tool_call_id="edit", name="manage_playlist_draft")
    assert validate_final_response("已移除草稿中的第三首，尚未保存。", [success], TOOLS) is None


def test_draft_creation_cannot_claim_playlist_saved():
    draft = ToolMessage(content=json.dumps({"status": "draft", "draft": {"status": "draft", "revision": 0}}), tool_call_id="preview", name="create_smart_playlist")
    assert validate_final_response("歌单已保存。", [draft], TOOLS).code == "smart_playlist_not_saved"


def test_read_after_success_does_not_discard_edit_or_save_receipt():
    draft = {"id": "d", "status": "draft", "revision": 1}
    edit = ToolMessage(content=json.dumps({"action": "remove", "previous_revision": 0, "draft": draft}), tool_call_id="edit", name="manage_playlist_draft")
    read = ToolMessage(content=json.dumps({"action": "read", "draft": draft}), tool_call_id="read", name="manage_playlist_draft")
    assert validate_final_response("已移除草稿第三首。", [edit, read], TOOLS) is None
    save = ToolMessage(content=json.dumps({"action": "confirm", "status": "saved", "draft": {**draft, "status": "saved"}, "playlist": {"id": "p"}}), tool_call_id="save", name="manage_playlist_draft")
    assert validate_final_response("草稿已保存，歌单已更新。", [save, read], TOOLS) is None
