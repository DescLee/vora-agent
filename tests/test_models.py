from pathlib import Path

from manus_mini.models import ContextSegment, LoopLimits, Message, SessionState, TaskState


def test_loop_limits_have_v1_defaults() -> None:
    limits = LoopLimits()
    assert limits.max_engineering_steps == 3
    assert limits.max_react_iterations == 99
    assert limits.max_reflection_rounds == 3
    assert limits.max_tool_calls_per_iteration == 99
    assert limits.max_tool_retries == 3
    assert limits.max_tool_timeout_seconds == 30
    assert not hasattr(limits, "max_runtime_seconds")
    assert limits.max_estimated_tokens == 128_000


def test_message_factory_methods_keep_tool_call_links() -> None:
    assistant = Message.agent("need file", tool_call_ids=["call-1", "call-2"])
    tool = Message.tool("file content", tool_call_id="call-1")

    assert assistant.role == "agent"
    assert assistant.tool_call_ids == ["call-1", "call-2"]
    assert tool.role == "tool"
    assert tool.tool_call_id == "call-1"


def test_session_state_starts_empty(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)

    assert session.schema_version == 1
    assert session.cwd == tmp_path
    assert session.messages == []
    assert session.active_task is None
    assert session.artifacts == []
    assert session.pending_confirmation is None
    assert session.run_ids == []


def test_task_state_uses_default_limits(tmp_path: Path) -> None:
    task = TaskState.create(goal="write report", cwd=tmp_path)

    assert task.goal == "write report"
    assert task.cwd == tmp_path
    assert task.limits == LoopLimits()
    assert task.step_count == 0
    assert task.result == ""


def test_context_segment_can_be_created() -> None:
    segment = ContextSegment(
        id="seg-1",
        kind="plain_message",
        messages=[Message.user("hello")],
        estimated_tokens=3,
        priority=10,
    )

    assert segment.id == "seg-1"
    assert segment.kind == "plain_message"
    assert segment.estimated_tokens == 3
    assert segment.priority == 10
