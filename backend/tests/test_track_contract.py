import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models import Track
import json

from services.track_contract import local_track_card, remote_track_card, track_cards_from_tool_result


def test_local_card_exposes_play_and_add_actions():
    track = Track(
        id="20261001/Coldplay-Yellow-BV1.mp3",
        title="Yellow",
        author="Coldplay",
        date="",
        filename="Coldplay-Yellow-BV1.mp3",
        subDir="20261001",
        size=1,
        url="/api/tracks/20261001/Coldplay-Yellow-BV1.mp3",
        bvid="BV1",
    )

    card = local_track_card(track)

    assert card.availability == "local"
    assert card.local_track == track
    assert card.download_status == "downloaded"
    assert "download" not in card.allowed_actions
    assert "play" in card.allowed_actions
    assert "add_to_session" in card.allowed_actions


def test_remote_card_only_exposes_download():
    card = remote_track_card(
        {"bvid": "BV123", "title": "七里香", "author": "Uploader", "duration": "4:59"}
    )

    assert card is not None
    assert card.track_id == "bilibili:BV123"
    assert card.allowed_actions == ["download"]
    assert card.local_track is None


def test_only_explicit_presentation_result_becomes_cards():
    card = remote_track_card({"bvid": "BV123", "title": "七里香", "author": "Uploader"})
    assert card is not None

    assert track_cards_from_tool_result(
        "bili_search", json.dumps({"videos": [card.model_dump(mode="json")]})
    ) == []
    presented = track_cards_from_tool_result(
        "present_tracks",
        json.dumps({"status": "presented", "tracks": [card.model_dump(mode="json")]}),
    )
    assert presented[0]["track_id"] == "bilibili:BV123"
