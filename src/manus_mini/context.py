from __future__ import annotations

from typing import Literal, Sequence

from manus_mini.models import Artifact, CompressionSnapshot, ContextBundle, ContextSegment, MemoryItem, Message, Observation
from manus_mini.redaction import redact_sensitive_text


class ContextIntegrityError(ValueError):
    pass


def estimate_tokens(text: str, kind: Literal["zh", "en", "code", "mixed"]) -> int:
    if kind == "zh":
        return int(len(text) * 1.2)
    if kind == "en":
        return int(len(text.split()) * 1.3)
    if kind == "code":
        return max(1, len(text) // 3)
    return max(1, len(text) // 2)


def validate_tool_call_pairs(messages: Sequence[Message]) -> None:
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]

        if message.role == "agent" and message.tool_call_ids:
            expected_ids = list(message.tool_call_ids)
            if len(expected_ids) != len(set(expected_ids)):
                raise ContextIntegrityError(
                    "context tool_call_id integrity failed: duplicate tool_call_ids in agent message"
                )

            index += 1
            while expected_ids:
                if index >= total:
                    raise ContextIntegrityError(
                        "context tool_call_id integrity failed: missing tool result for assistant tool_calls"
                    )

                next_message = messages[index]
                if next_message.role != "tool":
                    raise ContextIntegrityError(
                        "context tool_call_id integrity failed: tool_exchange must stay contiguous"
                    )
                if next_message.tool_call_id not in expected_ids:
                    raise ContextIntegrityError(
                        "context tool_call_id integrity failed: orphan tool_call_id "
                        f"{next_message.tool_call_id!r}"
                    )

                expected_ids.remove(next_message.tool_call_id)
                index += 1
            continue

        if message.role == "tool":
            raise ContextIntegrityError(
                "context tool_call_id integrity failed: orphan tool_call_id "
                f"{message.tool_call_id!r}"
            )

        index += 1


def build_segments(messages: Sequence[Message]) -> list[ContextSegment]:
    validate_tool_call_pairs(messages)

    segments: list[ContextSegment] = []
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]

        if message.role == "agent" and message.tool_call_ids:
            group = [message]
            expected_ids = list(message.tool_call_ids)
            index += 1
            while expected_ids:
                next_message = messages[index]
                group.append(next_message)
                expected_ids.remove(next_message.tool_call_id)
                index += 1

            segments.append(
                ContextSegment(
                    id=group[0].id,
                    kind="tool_exchange",
                    messages=group,
                    estimated_tokens=sum(estimate_tokens(item.content, "mixed") for item in group),
                    priority=50,
                )
            )
            continue

        segments.append(
            ContextSegment(
                id=message.id,
                kind="plain_message",
                messages=[message],
                estimated_tokens=estimate_tokens(message.content, "mixed"),
                priority=100,
            )
        )
        index += 1

    return segments


def compact_messages(messages: Sequence[Message], token_budget: int) -> list[Message]:
    compacted, _ = compact_messages_with_snapshot(messages, token_budget)
    return compacted


def estimate_message_tokens(messages: Sequence[Message]) -> int:
    return sum(max(1, len(message.content) // 2) for message in messages)


def build_context_bundle(
    current_user_message: Message,
    recent_messages: Sequence[Message],
    relevant_memories: Sequence[MemoryItem] | None = None,
    compression_summaries: Sequence[CompressionSnapshot] | None = None,
    active_artifacts: Sequence[Artifact] | None = None,
    recent_observations: Sequence[Observation] | None = None,
) -> ContextBundle:
    return ContextBundle(
        current_user_message=current_user_message,
        recent_messages=list(recent_messages),
        relevant_memories=list(relevant_memories or []),
        compression_summaries=list(compression_summaries or []),
        active_artifacts=list(active_artifacts or []),
        recent_observations=list(recent_observations or []),
    )


def compact_messages_with_snapshot(messages: Sequence[Message], token_budget: int) -> tuple[list[Message], CompressionSnapshot | None]:
    if not messages:
        return [], None

    segments = build_segments(messages)
    total_tokens = sum(segment.estimated_tokens for segment in segments)
    if total_tokens <= token_budget:
        return list(messages), None

    kept_reversed: list[ContextSegment] = []
    kept_tokens = 0
    for segment in reversed(segments):
        if kept_reversed and kept_tokens + segment.estimated_tokens > token_budget:
            break
        kept_reversed.append(segment)
        kept_tokens += segment.estimated_tokens

    kept_segments = list(reversed(kept_reversed))
    removed_count = len(segments) - len(kept_segments)
    if removed_count <= 0:
        return [message for segment in kept_segments for message in segment.messages], None

    covered_segments = segments[:removed_count]
    summary_text = _summarize_segments(covered_segments)
    summary = Message.system(summary_text)
    snapshot = CompressionSnapshot(
        covered_message_ids=[message.id for segment in covered_segments for message in segment.messages],
        covered_observation_ids=[],
        summary=summary_text,
        retained_facts=[line for line in summary_text.splitlines() if line.startswith("- ")],
    )
    compacted = [summary]
    for segment in kept_segments:
        compacted.extend(segment.messages)
    validate_tool_call_pairs(compacted)
    return compacted, snapshot


def _summarize_segments(segments: Sequence[ContextSegment]) -> str:
    parts = ["历史上下文摘要："]
    for segment in segments:
        if segment.kind == "tool_exchange":
            tool_ids = ", ".join(segment.messages[0].tool_call_ids)
            tool_summaries = [
                _preview(message.content)
                for message in segment.messages[1:]
                if message.role == "tool"
            ]
            parts.append(f"- 工具交换 {tool_ids}: {'; '.join(tool_summaries)}")
            continue

        message = segment.messages[0]
        speaker = "用户" if message.role == "user" else "Agent" if message.role == "agent" else message.role
        parts.append(f"- {speaker}: {_preview(message.content)}")
    return "\n".join(parts)


def _preview(content: str, limit: int = 160) -> str:
    compact = " ".join(redact_sensitive_text(content).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


__all__ = [
    "ContextIntegrityError",
    "build_segments",
    "build_context_bundle",
    "compact_messages",
    "compact_messages_with_snapshot",
    "estimate_message_tokens",
    "estimate_tokens",
    "validate_tool_call_pairs",
]
