import os
import re
import sys
from pathlib import Path

# Project root is the parent of the backend/ directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Platform detection
IS_WINDOWS: bool = sys.platform == "win32"
PLATFORM_HINT: str = "Windows" if IS_WINDOWS else "Linux"


def _resolve_music_dir() -> str:
    raw = os.getenv("MUSIC_DIR", "Documents/bili")
    p = Path(raw)
    if p.is_absolute():
        return str(p)
    return str(PROJECT_ROOT / p)


def _resolve_wiki_dir() -> str:
    raw = os.getenv("WIKI_DIR", "LLM-Wiki")
    p = Path(raw)
    if p.is_absolute():
        return str(p)
    return str(PROJECT_ROOT / p)


def _resolve_optional_path(env_name: str) -> str:
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return ""
    path = Path(raw)
    return str(path if path.is_absolute() else PROJECT_ROOT / path)


class Settings:
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv(
        "OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    MODEL_NAME: str = os.getenv("MODEL_NAME", "qwen3.5-flash")
    QWEN_AGENT_ENABLE_THINKING: bool = os.getenv(
        "QWEN_AGENT_ENABLE_THINKING", "true"
    ).strip().lower() in {"1", "true", "yes", "on"}
    QWEN_AGENT_THINKING_BUDGET: int = int(
        os.getenv("QWEN_AGENT_THINKING_BUDGET", "2048")
    )
    VOICE_ASR_MODEL: str = os.getenv("VOICE_ASR_MODEL", "qwen3-asr-flash")
    VOICE_TTS_MODEL: str = os.getenv(
        "VOICE_TTS_MODEL", "qwen-audio-3.0-tts-flash"
    )
    VOICE_TTS_VOICE: str = os.getenv("VOICE_TTS_VOICE", "longanhuan_v3.6")
    MUSIC_DIR: str = _resolve_music_dir()
    WIKI_DIR: str = _resolve_wiki_dir()
    BILIBILI_COOKIES_FILE: str = _resolve_optional_path("BILIBILI_COOKIES_FILE")
    BILIBILI_DOWNLOAD_TIMEOUT_SECONDS: int = max(
        60, min(int(os.getenv("BILIBILI_DOWNLOAD_TIMEOUT_SECONDS", "300")), 900)
    )
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("BACKEND_PORT", "8000"))
    DREAM_INTERVAL_HOURS: int = int(os.getenv("DREAM_INTERVAL_HOURS", "24"))
    SQLITE_JOURNAL_MODE: str = os.getenv("SQLITE_JOURNAL_MODE", "DELETE")

    # Keys that can be updated at runtime and persisted to .env.local
    _ENV_LOCAL_KEYS = {"OPENAI_API_KEY", "OPENAI_BASE_URL", "MODEL_NAME"}

    def update(self, **kwargs: str) -> None:
        """Update settings at runtime and persist to .env.local."""
        updates = {k: v for k, v in kwargs.items() if k in self._ENV_LOCAL_KEYS}
        for k, v in updates.items():
            setattr(self, k, v)
        if updates:
            self._write_env_local(updates)

    def _write_env_local(self, updates: dict[str, str]) -> None:
        """Write key=value pairs to backend/.env.local, updating existing lines."""
        env_path = Path(__file__).resolve().parent / ".env.local"
        lines: list[str] = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()

        updated_keys: set[str] = set()
        new_lines: list[str] = []
        for line in lines:
            m = re.match(r"^(export\s+)?([A-Z_]+)\s*=", line)
            if m and m.group(2) in updates:
                new_lines.append(f"{m.group(2)}={updates[m.group(2)]}")
                updated_keys.add(m.group(2))
            else:
                new_lines.append(line)

        for k, v in updates.items():
            if k not in updated_keys:
                new_lines.append(f"{k}={v}")

        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


settings = Settings()
