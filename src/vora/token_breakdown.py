from __future__ import annotations

import json
from typing import Any

from vora.context import estimate_tokens
from vora.llm import tool_schema
from vora.logging import EventLogger
from vora.models import Message


BREAKDOWN_PARTS = (
    "system_prompt",
    "project_overview",
    "current_task",
    "history_user",
    "assistant_messages",
    "tool_results",
    "tool_schema",
    "other",
)


def record_llm_token_breakdown(
    logger: EventLogger | None,
    session_id: str,
    run_id: str,
    *,
    stage: str,
    iteration: int,
    messages: list[Message],
    tool_names: list[str],
) -> None:
    if logger is None:
        return
    breakdown = estimate_llm_token_breakdown(messages, tool_names)
    logger.record(
        session_id,
        run_id,
        {
            "type": "llm_token_breakdown",
            "stage": stage,
            "iteration": iteration,
            **breakdown,
        },
    )


def estimate_llm_token_breakdown(messages: list[Message], tool_names: list[str]) -> dict[str, Any]:
    parts = {name: 0 for name in BREAKDOWN_PARTS}
    last_user_index = _last_user_message_index(messages)
    for index, message in enumerate(messages):
        tokens = estimate_tokens(message.content, "mixed")
        if message.role == "system":
            key = "project_overview" if "项目代码目录结构" in message.content else "system_prompt"
        elif message.role == "user":
            key = "current_task" if index == last_user_index else "history_user"
        elif message.role == "agent":
            key = "assistant_messages"
        elif message.role == "tool":
            key = "tool_results"
        else:
            key = "other"
        parts[key] += tokens

    tool_schema_tokens = sum(_estimate_tool_schema_tokens(name) for name in tool_names)
    parts["tool_schema"] = tool_schema_tokens
    return {
        "estimated_total_tokens": sum(parts.values()),
        "parts": parts,
        "message_count": len(messages),
        "tool_count": len(tool_names),
    }


def _last_user_message_index(messages: list[Message]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == "user":
            return index
    return None


def _estimate_tool_schema_tokens(name: str) -> int:
    payload = {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Tool {name}",
            "parameters": tool_schema(name),
        },
    }
    return estimate_tokens(json.dumps(payload, ensure_ascii=False, sort_keys=True), "mixed")
