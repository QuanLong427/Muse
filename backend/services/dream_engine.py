"""Evidence-based background consolidation for Musicer memory v2."""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from config import settings
from services.llm_client import completion_options, create_openai_client
from services.memory_manager import (
    init_memory_system,
    render_structured_memories,
    sync_profile_projection,
)
from services.memory_store import (
    DEFAULT_USER_ID,
    create_dream_run,
    finish_dream_run,
    get_pending_messages,
    mark_messages_processed,
    stage_candidate,
)
from services.scenario_manager import read_scenarios


logger = logging.getLogger(__name__)
_dream_lock = threading.Lock()


DREAM_SYSTEM_PROMPT = """你是 Musicer 的记忆候选提取器，不是用户画像的自由写作者。

你只能从标记为 user 的原始消息提取候选，禁止把 Agent 回答、网络内容或猜测当成用户偏好。
输出一个 JSON 对象，不要输出 Markdown：
{
  "candidates": [
    {
      "kind": "preference|avoidance|interaction|language|music_fact",
      "scenario": "全局或给定场景",
      "memory_key": "稳定、简短、可用于覆盖旧值的键",
      "directive": "可直接指导未来行为的中文指令",
      "confidence": 0.0,
      "evidence_type": "explicit_preference|explicit_correction|repeated_behavior|temporary_request|agent_inference|external_content",
      "source_message_ids": [123]
    }
  ]
}

规则：
1. “我喜欢/不要/以后都/记住/纠正一下”等明确表达可标 explicit_preference 或 explicit_correction。
2. “今天/这次/现在想听”属于 temporary_request，不得伪装成长期偏好。
3. 只出现一次的播放或搜索请求通常不是长期偏好。
4. source_message_ids 必须逐字使用输入中的消息 ID，不得编造。
5. 没有可靠候选时返回 {"candidates": []}。
6. 不要复述当前长期记忆中已经完全相同的指令。"""

_ALLOWED_KINDS = {"preference", "avoidance", "interaction", "language", "music_fact"}
_ALLOWED_EVIDENCE = {
    "explicit_preference",
    "explicit_correction",
    "repeated_behavior",
    "temporary_request",
    "agent_inference",
    "external_content",
}


def _extract_json(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", stripped, re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Dream did not return a JSON object")
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict) or not isinstance(value.get("candidates"), list):
        raise ValueError("Dream JSON must contain a candidates array")
    return value


def _build_dream_prompt(
    user_messages: list[dict[str, Any]],
    current_memory: str,
    scenarios: list[str],
) -> str:
    evidence = [
        {
            "id": item["id"],
            "created_at": item["timestamp"],
            "scenario": item.get("scenario") or "默认",
            "role": "user",
            "content": item["content"],
        }
        for item in user_messages
    ]
    return (
        "可用场景："
        + json.dumps(["全局", *scenarios], ensure_ascii=False)
        + "\n当前已生效的结构化记忆：\n"
        + (current_memory or "（无）")
        + "\n\n本批唯一可信证据（JSON）：\n"
        + json.dumps(evidence, ensure_ascii=False, indent=2)
    )


def _validate_candidate(
    raw: Any,
    *,
    allowed_source_ids: set[int],
    scenarios: set[str],
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind", "")).strip()
    evidence_type = str(raw.get("evidence_type", "")).strip()
    scenario = str(raw.get("scenario", "默认")).strip() or "默认"
    memory_key = str(raw.get("memory_key", "")).strip()
    directive = str(raw.get("directive", "")).strip()
    try:
        confidence = float(raw.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    try:
        sources = sorted({int(value) for value in raw.get("source_message_ids", [])})
    except (TypeError, ValueError):
        return None
    if kind not in _ALLOWED_KINDS or evidence_type not in _ALLOWED_EVIDENCE:
        return None
    if scenario not in scenarios or not memory_key or not directive:
        return None
    if not sources or any(source not in allowed_source_ids for source in sources):
        return None
    return {
        "kind": kind,
        "scenario": scenario,
        "memory_key": memory_key[:160],
        "directive": directive[:1000],
        "confidence": max(0.0, min(confidence, 1.0)),
        "evidence_type": evidence_type,
        "source_message_ids": sources,
    }


def run_dream(user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
    """Extract, gate and promote structured memories from pending messages."""
    if not _dream_lock.acquire(blocking=False):
        return {
            "status": "busy",
            "processed_count": 0,
            "message": "Dream 已在运行，本次请求已跳过",
        }
    run_id: str | None = None
    try:
        init_memory_system()
        pending = get_pending_messages(user_id, limit=100)
        if not pending:
            return {
                "status": "no_new_data",
                "processed_count": 0,
                "message": "没有新的对话记录需要处理",
            }

        run_id = create_dream_run(user_id)
        user_messages = [item for item in pending if item["role"] == "user"]
        pending_ids = [int(item["id"]) for item in pending]
        if not user_messages:
            mark_messages_processed(pending_ids)
            finish_dream_run(
                run_id,
                status="success",
                processed_count=len(pending),
                detail={"reason": "no_user_evidence"},
            )
            return {
                "status": "success",
                "processed_count": len(pending),
                "promoted_count": 0,
                "message": "已处理记录，但没有可用于记忆的用户证据",
            }

        scenarios = read_scenarios()
        current_memory = render_structured_memories(user_id)
        client = create_openai_client()
        response = client.chat.completions.create(
            model=settings.MODEL_NAME,
            messages=[
                {"role": "system", "content": DREAM_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": _build_dream_prompt(user_messages, current_memory, scenarios),
                },
            ],
            temperature=0.1,
            max_completion_tokens=2048,
            **completion_options("plain"),
        )
        parsed = _extract_json(response.choices[0].message.content or "")
        allowed_source_ids = {int(item["id"]) for item in user_messages}
        allowed_scenarios = {"全局", *scenarios}

        results = []
        for raw in parsed["candidates"][:30]:
            candidate = _validate_candidate(
                raw,
                allowed_source_ids=allowed_source_ids,
                scenarios=allowed_scenarios,
            )
            if not candidate:
                results.append({"status": "rejected", "reason": "候选结构或来源无效"})
                continue
            results.append(stage_candidate(user_id=user_id, **candidate))

        counts = {
            name: sum(1 for item in results if item["status"] == name)
            for name in ("promoted", "pending", "rejected")
        }
        # Mark the batch only after parsing and staging completed successfully.
        mark_messages_processed(pending_ids)
        if counts["promoted"]:
            sync_profile_projection(user_id)
        finish_dream_run(
            run_id,
            status="success",
            processed_count=len(pending),
            promoted_count=counts["promoted"],
            pending_count=counts["pending"],
            rejected_count=counts["rejected"],
            detail={"candidate_count": len(results)},
        )
        return {
            "status": "success",
            "processed_count": len(pending),
            "promoted_count": counts["promoted"],
            "pending_count": counts["pending"],
            "rejected_count": counts["rejected"],
            "message": (
                f"处理 {len(pending)} 条记录：晋升 {counts['promoted']}，"
                f"待观察 {counts['pending']}，拒绝 {counts['rejected']}"
            ),
        }
    except Exception as exc:
        logger.exception("[dream] consolidation failed")
        if run_id:
            finish_dream_run(
                run_id,
                status="error",
                processed_count=0,
                detail={"error": str(exc)},
            )
        return {
            "status": "error",
            "processed_count": 0,
            "message": f"Dream 失败: {exc}",
        }
    finally:
        _dream_lock.release()
