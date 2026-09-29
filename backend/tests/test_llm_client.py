import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import settings
from services.llm_client import completion_options, create_chat_model, is_qwen_model


def test_qwen_model_detection():
    assert is_qwen_model("qwen3.5-flash") is True
    assert is_qwen_model("QWEN3.5-FLASH-2026-02-23") is True
    assert is_qwen_model("deepseek-chat") is False


def test_qwen_structured_output_disables_thinking(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_NAME", "qwen3.5-flash")

    assert completion_options("structured") == {
        "response_format": {"type": "json_object"},
        "extra_body": {"enable_thinking": False},
    }


def test_qwen_agent_uses_bounded_thinking(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_NAME", "qwen3.5-flash")
    monkeypatch.setattr(settings, "QWEN_AGENT_ENABLE_THINKING", True)
    monkeypatch.setattr(settings, "QWEN_AGENT_THINKING_BUDGET", 2048)

    assert completion_options("agent") == {
        "extra_body": {
            "enable_thinking": True,
            "thinking_budget": 2048,
        }
    }


def test_chat_model_places_response_format_in_model_kwargs(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_NAME", "qwen3.5-flash")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        settings,
        "OPENAI_BASE_URL",
        "https://example.com/compatible-mode/v1",
    )

    model = create_chat_model(
        purpose="structured",
        max_completion_tokens=256,
        streaming=False,
    )

    assert model.model_kwargs["response_format"] == {"type": "json_object"}
    assert model.extra_body == {"enable_thinking": False}


def test_chat_model_caps_thinking_budget_below_output_limit(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_NAME", "qwen3.5-flash")
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(settings, "QWEN_AGENT_ENABLE_THINKING", True)
    monkeypatch.setattr(settings, "QWEN_AGENT_THINKING_BUDGET", 2048)

    model = create_chat_model(
        purpose="agent",
        max_completion_tokens=512,
        streaming=True,
    )

    assert model.extra_body == {
        "enable_thinking": True,
        "thinking_budget": 511,
    }


def test_non_qwen_does_not_receive_qwen_parameters(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_NAME", "other-compatible-model")
    assert completion_options("agent") == {}
