"""Qwen speech input/output adapters used by the web voice controls.

Speech is intentionally kept outside the LangGraph agent. ASR produces the
same text that a user would type, and TTS consumes only the final assistant
reply. This keeps the existing ReAct tools, skills, and memory semantics
unchanged.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from config import settings


MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_TTS_TEXT_LENGTH = 2000


class VoiceServiceError(RuntimeError):
    """A safe-to-display speech provider or configuration error."""


@dataclass(frozen=True)
class SynthesizedAudio:
    content: bytes
    media_type: str


def _require_api_key() -> str:
    key = settings.OPENAI_API_KEY.strip()
    if not key or key == "your-api-key-here":
        raise VoiceServiceError("尚未配置可用的阿里云百炼 API Key")
    return key


def _chat_completions_url() -> str:
    base = settings.OPENAI_BASE_URL.strip().rstrip("/")
    if not base:
        raise VoiceServiceError("尚未配置模型 Base URL")
    return f"{base}/chat/completions"


def _speech_synthesizer_url() -> str:
    """Derive the native DashScope TTS endpoint from the configured host."""
    parsed = urlsplit(settings.OPENAI_BASE_URL.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VoiceServiceError("模型 Base URL 格式无效")
    return (
        f"{parsed.scheme}://{parsed.netloc}"
        "/api/v1/services/audio/tts/SpeechSynthesizer"
    )


def _provider_error(response: httpx.Response, operation: str) -> VoiceServiceError:
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            message = str(
                payload.get("message")
                or payload.get("error", {}).get("message", "")
            ).strip()
    except (ValueError, TypeError, AttributeError):
        pass
    suffix = f"：{message[:300]}" if message else ""
    return VoiceServiceError(f"{operation}失败（HTTP {response.status_code}）{suffix}")


async def transcribe_audio(audio: bytes, media_type: str) -> str:
    if not audio:
        raise VoiceServiceError("录音内容为空")
    if len(audio) > MAX_AUDIO_BYTES:
        raise VoiceServiceError("录音超过 10MB 限制，请缩短后重试")

    normalized_type = (media_type or "application/octet-stream").split(";", 1)[0]
    data_uri = f"data:{normalized_type};base64,{base64.b64encode(audio).decode('ascii')}"
    payload = {
        "model": settings.VOICE_ASR_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": data_uri},
                    }
                ],
            }
        ],
        "stream": False,
        "asr_options": {"enable_itn": True},
    }

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                _chat_completions_url(),
                headers={
                    "Authorization": f"Bearer {_require_api_key()}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise VoiceServiceError(f"语音识别网络请求失败：{exc}") from exc

    if not response.is_success:
        raise _provider_error(response, "语音识别")

    try:
        result = response.json()
        text = result["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise VoiceServiceError("语音识别返回了无法解析的结果") from exc
    if not isinstance(text, str) or not text.strip():
        raise VoiceServiceError("没有识别到有效语音")
    return text.strip()


def _extract_audio_output(payload: dict[str, Any]) -> tuple[str | None, bytes | None]:
    output = payload.get("output")
    if not isinstance(output, dict):
        return None, None
    audio = output.get("audio")
    if not isinstance(audio, dict):
        return None, None

    url = audio.get("url")
    data = audio.get("data")
    decoded = None
    if isinstance(data, str) and data:
        try:
            decoded = base64.b64decode(data, validate=True)
        except ValueError:
            decoded = None
    return url if isinstance(url, str) and url else None, decoded


async def synthesize_speech(text: str) -> SynthesizedAudio:
    normalized = text.strip()
    if not normalized:
        raise VoiceServiceError("待播报文本为空")
    if len(normalized) > MAX_TTS_TEXT_LENGTH:
        raise VoiceServiceError(f"待播报文本不能超过 {MAX_TTS_TEXT_LENGTH} 个字符")

    payload = {
        "model": settings.VOICE_TTS_MODEL,
        "input": {
            "text": normalized,
            "voice": settings.VOICE_TTS_VOICE,
            "format": "wav",
            "sample_rate": 24000,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            response = await client.post(
                _speech_synthesizer_url(),
                headers={
                    "Authorization": f"Bearer {_require_api_key()}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if not response.is_success:
                raise _provider_error(response, "语音合成")

            try:
                result = response.json()
            except ValueError as exc:
                raise VoiceServiceError("语音合成返回了无法解析的结果") from exc
            audio_url, audio_data = _extract_audio_output(result)
            if audio_data:
                return SynthesizedAudio(audio_data, "audio/wav")
            if not audio_url:
                raise VoiceServiceError("语音合成结果中缺少音频")

            parsed = urlsplit(audio_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise VoiceServiceError("语音合成返回了无效的音频地址")
            audio_response = await client.get(audio_url)
            if not audio_response.is_success:
                raise _provider_error(audio_response, "合成音频下载")
    except VoiceServiceError:
        raise
    except httpx.HTTPError as exc:
        raise VoiceServiceError(f"语音合成网络请求失败：{exc}") from exc

    if not audio_response.content:
        raise VoiceServiceError("语音合成返回了空音频")
    return SynthesizedAudio(
        audio_response.content,
        audio_response.headers.get("content-type", "audio/wav").split(";", 1)[0],
    )
