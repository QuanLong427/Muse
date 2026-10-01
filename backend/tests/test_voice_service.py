import asyncio
import base64

import httpx

from config import settings
from services import voice_service


class FakeResponse:
    def __init__(
        self,
        payload=None,
        *,
        status_code=200,
        content=b"",
        headers=None,
    ):
        self._payload = payload
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._payload


def test_speech_endpoint_uses_configured_workspace_host(monkeypatch):
    monkeypatch.setattr(
        settings,
        "OPENAI_BASE_URL",
        "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )
    assert voice_service._speech_synthesizer_url() == (
        "https://workspace.cn-beijing.maas.aliyuncs.com"
        "/api/v1/services/audio/tts/SpeechSynthesizer"
    )


def test_transcribe_audio_sends_data_uri(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["request"] = kwargs
            return FakeResponse(
                {"choices": [{"message": {"content": "播放晴天"}}]}
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        settings, "OPENAI_BASE_URL", "https://example.com/compatible-mode/v1"
    )

    result = asyncio.run(voice_service.transcribe_audio(b"audio", "audio/webm"))

    assert result == "播放晴天"
    assert captured["url"].endswith("/compatible-mode/v1/chat/completions")
    data = captured["request"]["json"]["messages"][0]["content"][0][
        "input_audio"
    ]["data"]
    assert data == f"data:audio/webm;base64,{base64.b64encode(b'audio').decode()}"
    assert captured["request"]["json"]["asr_options"]["enable_itn"] is True


def test_synthesize_speech_downloads_provider_audio(monkeypatch):
    captured = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured.append(("post", url, kwargs))
            return FakeResponse(
                {"output": {"audio": {"url": "https://audio.example/reply.wav"}}}
            )

        async def get(self, url):
            captured.append(("get", url, {}))
            return FakeResponse(
                content=b"RIFF-audio",
                headers={"content-type": "audio/wav"},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        settings, "OPENAI_BASE_URL", "https://workspace.example/compatible-mode/v1"
    )

    result = asyncio.run(voice_service.synthesize_speech("你好"))

    assert result.content == b"RIFF-audio"
    assert result.media_type == "audio/wav"
    assert captured[0][0] == "post"
    assert captured[0][2]["json"]["input"]["voice"] == settings.VOICE_TTS_VOICE
    assert captured[1] == ("get", "https://audio.example/reply.wav", {})


def test_transcribe_rejects_audio_over_provider_limit():
    oversized = b"x" * (voice_service.MAX_AUDIO_BYTES + 1)
    try:
        asyncio.run(voice_service.transcribe_audio(oversized, "audio/wav"))
    except voice_service.VoiceServiceError as exc:
        assert "10MB" in str(exc)
    else:
        raise AssertionError("oversized audio should have been rejected")
