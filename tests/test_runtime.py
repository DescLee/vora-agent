from pathlib import Path

import pytest

from manus_mini.llm import MockLLMClient
from manus_mini.models import LoopLimits, Message, SessionState, TaskState, ToolCall
from manus_mini.react import ReActLoop
from manus_mini.reflection import ReflectionLoop
from manus_mini.runtime import AgentRuntime
from manus_mini.tools.registry import ToolRegistry


def test_runtime_turns_user_request_into_agent_reply(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello world", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime()

    result = runtime.on_user_message("读取 a.md", session)

    assert result.active_task is not None
    assert result.messages[-1].role == "agent"
    assert "hello world" in result.messages[-1].content
    assert any(observation.tool_call_id == "call-read-a" for observation in result.active_task.observations)


def test_react_loop_raises_when_budget_is_zero(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="循环测试", cwd=tmp_path, limits=LoopLimits(max_react_iterations=0))

    with pytest.raises(RuntimeError, match="MAX_REACT_ITERATIONS_REACHED"):
        ReActLoop(MockLLMClient(), ToolRegistry()).run(task, session)


def test_reflection_loop_keeps_best_result_when_round_budget_is_zero(tmp_path: Path) -> None:
    class FakeReactLoop:
        def run(self, task: TaskState, session: SessionState) -> str:  # noqa: ARG002
            return "当前最佳结果"

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path, limits=LoopLimits(max_reflection_rounds=0))
    reflection = ReflectionLoop(react_loop=FakeReactLoop())

    result = reflection.run(task, session)

    assert result.accepted is True
    assert result.content == "当前最佳结果"
    assert result.reason in {"accepted", "max reflection rounds reached"}


def test_runtime_respects_engineering_step_limit(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(default_limits=LoopLimits(max_engineering_steps=1))

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    assert result.active_task.step_count == 1
