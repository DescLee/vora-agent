from manus_mini.context import (
    ContextIntegrityError,
    build_segments,
    compact_messages,
    estimate_tokens,
    validate_tool_call_pairs,
)
from manus_mini.models import Message


def test_estimate_tokens_uses_v1_rules() -> None:
    assert estimate_tokens("abcd", "mixed") == 2
    assert estimate_tokens("中文", "zh") == 2
    assert estimate_tokens("one two", "en") == 2
    assert estimate_tokens("print('hello')", "code") == 4


def test_validate_tool_call_pairs_accepts_complete_tool_exchange() -> None:
    messages = [
        Message.user("start"),
        Message.agent("need file", tool_call_ids=["call-1", "call-2"]),
        Message.tool("file content", tool_call_id="call-1"),
        Message.tool("more content", tool_call_id="call-2"),
        Message.user("next"),
    ]

    validate_tool_call_pairs(messages)


def test_validate_tool_call_pairs_rejects_orphan_tool_result() -> None:
    messages = [Message.tool("orphan", tool_call_id="call-1")]

    try:
        validate_tool_call_pairs(messages)
    except ContextIntegrityError as exc:
        assert "tool_call_id" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ContextIntegrityError")


def test_build_segments_keeps_tool_exchange_together() -> None:
    messages = [
        Message.user("start"),
        Message.agent("need file", tool_call_ids=["call-1"]),
        Message.tool("file content", tool_call_id="call-1"),
        Message.user("done"),
    ]

    segments = build_segments(messages)

    assert [segment.kind for segment in segments] == [
        "plain_message",
        "tool_exchange",
        "plain_message",
    ]
    assert [message.role for message in segments[1].messages] == ["agent", "tool"]
    assert segments[1].messages[0].tool_call_ids == ["call-1"]
    assert segments[1].messages[1].tool_call_id == "call-1"


def test_compact_messages_summarizes_old_messages_without_splitting_tool_exchange() -> None:
    messages = [
        Message.user("很早以前的需求：" + "A" * 80),
        Message.agent("need file", tool_call_ids=["call-1"]),
        Message.tool("file content " + "B" * 80, tool_call_id="call-1"),
        Message.user("最新需求：请基于上文继续"),
    ]

    compacted = compact_messages(messages, token_budget=30)

    validate_tool_call_pairs(compacted)
    assert compacted[0].role == "system"
    assert compacted[0].content.startswith("历史上下文摘要：")
    assert compacted[-1].content == "最新需求：请基于上文继续"
    assert not any(message.role == "tool" for message in compacted[1:])


def test_compact_messages_redacts_sensitive_content_in_summary() -> None:
    messages = [
        Message.user("旧需求包含 API_KEY=sk-live-secret " + "A" * 80),
        Message.agent("need file", tool_call_ids=["call-1"]),
        Message.tool("文件里有 password=abc123 " + "B" * 80, tool_call_id="call-1"),
        Message.user("最新需求"),
    ]

    compacted = compact_messages(messages, token_budget=20)

    assert compacted[0].role == "system"
    assert "sk-live-secret" not in compacted[0].content
    assert "password=abc123" not in compacted[0].content
    assert "[REDACTED]" in compacted[0].content
