"""Deprecated compatibility facade for the old ``/api/playlist`` endpoint.

The old table represented the active playback plan, not a named playlist.
New code must use :mod:`services.playback_session_store`.
"""

from typing import Dict, List

from services.playback_session_store import (
    get_legacy_tracks,
    init_playback_session_db,
    replace_legacy_tracks,
)


def init_playlist_db() -> None:
    init_playback_session_db()


def get_playlist() -> List[Dict]:
    return get_legacy_tracks()


def set_playlist(tracks: List[Dict]) -> None:
    replace_legacy_tracks(tracks)
