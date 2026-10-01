import os
import sys

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.agent_protocol import validate_final_response


TOOLS = {
    "local_search",
    "bili_search",
    "convert_video",
    "control_player",
    "manage_playback_session",
    "set_playback_mode",
    "record_track_feedback",
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
