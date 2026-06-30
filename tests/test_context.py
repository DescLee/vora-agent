from manus_mini.context import (
    ContextIntegrityError,
    build_segments,
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
