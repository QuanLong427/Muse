"""Shared Qwen/OpenAI-compatible client configuration.

Musicer uses the OpenAI-compatible Chat Completions endpoint exposed by
Alibaba Cloud Model Studio.  Keep provider-specific request options here so
Wiki ingestion, the conversational agent, and Dream do not drift apart.
"""

from __future__ import annotations

from typing import Any, Literal

from config import settings

LLMPurpose = Literal["structured", "agent", "plain"]


def is_qwen_model(model: str | None = None) -> bool:
    """Return whether the configured model is a Qwen model."""
    return (model or settings.MODEL_NAME).strip().lower().startswith("qwen")


def completion_options(purpose: LLMPurpose = "plain") -> dict[str, Any]:
    """Build request options for the configured provider and workload.

    Qwen JSON mode cannot be combined with thinking mode.  Deterministic
    extraction/summarisation therefore disables thinking, while the main
    tool-using agent gets a bounded reasoning budget.
    """
    if not is_qwen_model():
        return {}

    if purpose == "structured":
        return {
            "response_format": {"type": "json_object"},
            "extra_body": {"enable_thinking": False},
        }

    if purpose == "agent":
        extra_body: dict[str, Any] = {
            "enable_thinking": settings.QWEN_AGENT_ENABLE_THINKING,
        }
        if settings.QWEN_AGENT_ENABLE_THINKING:
            extra_body["thinking_budget"] = settings.QWEN_AGENT_THINKING_BUDGET
        return {"extra_body": extra_body}

    return {"extra_body": {"enable_thinking": False}}


def create_openai_client():
    """Create an OpenAI SDK client for the configured compatible endpoint."""
    from openai import OpenAI

    return OpenAI(
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
    )


def create_chat_model(
    *,
    purpose: LLMPurpose = "plain",
    max_completion_tokens: int,
    streaming: bool,
):
    """Create a LangChain chat model with Qwen-compatible request options."""
    from langchain_openai import ChatOpenAI

    options = completion_options(purpose)
    response_format = options.pop("response_format", None)
    model_kwargs = {"response_format": response_format} if response_format else {}
    extra_body = options.get("extra_body")
    if extra_body and extra_body.get("enable_thinking") and "thinking_budget" in extra_body:
        # Qwen requires max_completion_tokens to be greater than thinking_budget.
        options["extra_body"] = {
            **extra_body,
            "thinking_budget": min(
                int(extra_body["thinking_budget"]),
                max(1, max_completion_tokens - 1),
            ),
        }

    return ChatOpenAI(
        model=settings.MODEL_NAME,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        max_completion_tokens=max_completion_tokens,
        streaming=streaming,
        model_kwargs=model_kwargs,
        **options,
    )
