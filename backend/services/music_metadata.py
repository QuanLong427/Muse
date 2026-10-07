"""Normalize semantic metadata without renaming files or inventing identities."""
import re

_UNKNOWN_ARTISTS = {"unknown", "unknownartist", "unknownperformer", "none", "null", "na", "未知", "未知歌手", "未知艺术家", "不详", "歌手待核实"}
_NON_MUSIC = re.compile(r"\basmr\b|敲击音|抓挠音|口腔音|耳骚|触发音|轻语|白噪音|环境音|助眠音|有声书|游戏实况", re.I)


def normalize_artist(value) -> str:
    artist = str(value or "").strip()
    key = re.sub(r"[\W_]+", "", artist.casefold())
    return "" if not key or key in _UNKNOWN_ARTISTS else artist


def is_non_music_source(title: str) -> bool:
    return bool(_NON_MUSIC.search(title))
