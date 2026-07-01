import json
from pathlib import Path
import re
from time import perf_counter, sleep

import pytest

from manus_mini.llm import LLMResult, MockLLMClient
from manus_mini.models import LoopLimits, Message, Observation, SessionState, TaskState, ToolCall, TraceEvent
from manus_mini.react import ReActLoop
from manus_mini.reflection import ReflectionLoop, ReflectionResult
from manus_mini.logging import EventLogger
from manus_mini.reporter import Reporter
from manus_mini.runtime import AgentRuntime
from manus_mini.session import SessionManager
from manus_mini.tools.base import ToolResult
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
    assert any(event.phase == "llm" for event in result.active_task.trace_events)
    assert any(event.data.get("tool_name") == "read_file" for event in result.active_task.trace_events)


def test_runtime_project_summary_uses_project_files(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# manus-mini\n\nTUI Agent runtime.", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = \"manus-mini\"\n", encoding="utf-8")
    (tmp_path / "docs" / "v1-technical-design.md").write_text("三层 Agent Loop、工具调度、长期记忆。", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime()

    result = runtime.on_user_message("请你获取一下当前项目，并说明下它的作用", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert "manus-mini" in result.messages[-1].content
    assert "TUI" in result.messages[-1].content
    tool_events = [event for event in result.active_task.trace_events if event.phase == "tool"]
    assert any(event.data.get("tool_name") == "list_files" for event in tool_events)
    assert any(event.data.get("tool_name") == "read_file" for event in tool_events)
    assert runtime.memory_manager.search("manus-mini", limit=5)


def test_runtime_output_file_records_input_process_observations_and_result_in_chunks(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello world", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(reporter=Reporter(tmp_path / "outputs"))

    result = runtime.on_user_message("读取 a.md", session)

    assert result.active_task is not None
    artifact_path = result.active_task.artifacts[-1].path
    content = artifact_path.read_text(encoding="utf-8")

    assert "# Manus Mini Run" in content
    assert "## 1. 用户输入" in content
    assert "读取 a.md" in content
    assert "## 2. 执行过程" in content
    assert "### 2.1" in content
    assert "ReAct iteration" in content
    assert "## 3. 工具观察" in content
    assert "call-read-a" in content
    assert "hello world" in content
    assert "## 4. 最终产物" in content
    assert result.messages[-1].content in content
    summary_path = tmp_path / "runs" / result.active_task.run_id / "summary.md"
    assert summary_path.exists()
    assert "Manus Mini Run Summary" in summary_path.read_text(encoding="utf-8")


def test_runtime_output_filename_starts_with_timestamp_for_lookup(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(reporter=Reporter(tmp_path / "outputs"))

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    filename = result.active_task.artifacts[-1].path.name
    assert re.match(r"^\d{8}-\d{6}-run-[a-f0-9]{12}\.md$", filename)


def test_react_loop_raises_when_budget_is_zero(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="循环测试", cwd=tmp_path, limits=LoopLimits(max_react_iterations=0))

    with pytest.raises(RuntimeError, match="MAX_REACT_ITERATIONS_REACHED"):
        ReActLoop(MockLLMClient(), ToolRegistry()).run(task, session)


def test_react_loop_preserves_assistant_reasoning_content_between_tool_rounds(tmp_path: Path) -> None:
    class RecordingLLM:
        def __init__(self) -> None:
            self.calls = []

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls.append(messages)
            if len(self.calls) == 1:
                return LLMResult(
                    content="我需要读取 README。",
                    reasoning_content="Need project docs.",
                    tool_calls=[
                        ToolCall(
                            id="call-read-readme",
                            name="read_file",
                            args={"path": "README.md"},
                        )
                    ],
                    tool_call_arguments={"call-read-readme": '{"path":"README.md"}'},
                )
            assistant_messages = [message for message in messages if message.role == "agent" and message.tool_call_ids]
            assert assistant_messages
            assert assistant_messages[-1].metadata["reasoning_content"] == "Need project docs."
            assert assistant_messages[-1].metadata["tool_call_arguments"]["call-read-readme"] == '{"path":"README.md"}'
            return LLMResult(content="总结完成")

    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    llm = RecordingLLM()

    result = ReActLoop(llm, ToolRegistry()).run(task, session)

    assert result == "总结完成"


def test_react_loop_executes_independent_tool_batch_in_parallel(tmp_path: Path) -> None:
    class SlowReadOnlyTool:
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def __init__(self, name: str) -> None:
            self.name = name

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201
            sleep(0.2)
            return ToolResult(tool_name=self.name, ok=True, summary=f"{self.name} done")

    class ParallelLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(id="call-a", name="slow_a"),
                        ToolCall(id="call-b", name="slow_b"),
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert {message.tool_call_id for message in tool_messages} == {"call-a", "call-b"}
            return LLMResult(content="done")

    registry = ToolRegistry(tools=[SlowReadOnlyTool("slow_a"), SlowReadOnlyTool("slow_b")])
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="并行测试", cwd=tmp_path)

    started = perf_counter()
    result = ReActLoop(ParallelLLM(), registry).run(task, session)
    elapsed = perf_counter() - started

    assert result == "done"
    assert elapsed < 0.35


def test_react_loop_converts_unknown_tool_call_to_tool_observation(tmp_path: Path) -> None:
    class UnknownToolLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-missing", name="missing_tool")])
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert tool_messages[-1].tool_call_id == "call-missing"
            assert "UNKNOWN_TOOL" in tool_messages[-1].content
            return LLMResult(content="已处理未知工具")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="未知工具测试", cwd=tmp_path)

    result = ReActLoop(UnknownToolLLM(), ToolRegistry()).run(task, session)

    assert result == "已处理未知工具"
    assert task.observations[-1].ok is False
    assert task.trace_events[-1].phase == "llm"


def test_react_loop_includes_recent_conversation_context_for_follow_up(tmp_path: Path) -> None:
    class ContextAwareLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            text = "\n".join(message.content for message in messages)
            assert "上一版报告：需要保留技术亮点" in text
            return LLMResult(content="已基于上一版报告修改")

    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("写一份项目报告"))
    session.messages.append(Message.agent("上一版报告：需要保留技术亮点"))
    task = TaskState.create(goal="把上面的报告改短一点", cwd=tmp_path)

    result = ReActLoop(ContextAwareLLM(), ToolRegistry()).run(task, session)

    assert result == "已基于上一版报告修改"


def test_react_loop_normalizes_empty_and_duplicate_tool_call_ids(tmp_path: Path) -> None:
    class DuplicateToolIdLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(id="", name="list_files", args={"path": "."}),
                        ToolCall(id="", name="list_files", args={"path": "."}),
                    ]
                )
            tool_ids = [message.tool_call_id for message in messages if message.role == "tool"]
            assert len(tool_ids) == 2
            assert len(set(tool_ids)) == 2
            assert all(tool_ids)
            return LLMResult(content="ok")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="读取目录", cwd=tmp_path)

    result = ReActLoop(DuplicateToolIdLLM(), ToolRegistry()).run(task, session)

    assert result == "ok"


def test_react_loop_retries_transient_tool_failure(tmp_path: Path) -> None:
    class FlakyTool:
        name = "flaky"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def __init__(self) -> None:
            self.calls = 0

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201
            self.calls += 1
            if self.calls == 1:
                return ToolResult(tool_name=self.name, ok=False, summary="temporary failure", error_code="TOOL_TIMEOUT")
            return ToolResult(tool_name=self.name, ok=True, summary="recovered", content="ok")

    class RetryLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-flaky", name="flaky")])
            tool_messages = [message for message in messages if message.role == "tool"]
            assert "recovered" in tool_messages[-1].content
            return LLMResult(content="ok")

    tool = FlakyTool()
    registry = ToolRegistry(tools=[tool])
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="重试工具", cwd=tmp_path, limits=LoopLimits(max_tool_retries=1))

    result = ReActLoop(RetryLLM(), registry).run(task, session)

    assert result == "ok"
    assert tool.calls == 2
    assert task.observations[-1].ok is True


def test_react_loop_marks_tool_timeout(tmp_path: Path) -> None:
    class SlowTool:
        name = "slow"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201
            sleep(0.2)
            return ToolResult(tool_name=self.name, ok=True, summary="done")

    class TimeoutLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-slow", name="slow")])
            return LLMResult(content="timeout handled")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="工具超时", cwd=tmp_path, limits=LoopLimits(max_tool_timeout_seconds=0))

    result = ReActLoop(TimeoutLLM(), ToolRegistry(tools=[SlowTool()])).run(task, session)

    assert result == "timeout handled"
    assert task.observations[-1].ok is False
    assert task.observations[-1].summary == "tool execution timed out"


def test_react_loop_marks_tool_retry_exhausted(tmp_path: Path) -> None:
    class AlwaysFailTool:
        name = "always_fail"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201
            return ToolResult(tool_name=self.name, ok=False, summary="still failing", error_code="TOOL_TIMEOUT")

    class RetryExhaustedLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-fail", name="always_fail")])
            tool_messages = [message for message in messages if message.role == "tool"]
            assert "TOOL_RETRY_EXHAUSTED" in tool_messages[-1].content
            return LLMResult(content="fallback")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="重试耗尽", cwd=tmp_path, limits=LoopLimits(max_tool_retries=1))

    result = ReActLoop(RetryExhaustedLLM(), ToolRegistry(tools=[AlwaysFailTool()])).run(task, session)

    assert result == "fallback"
    assert task.observations[-1].ok is False
    assert task.observations[-1].summary == "still failing"


def test_react_loop_sanitizes_llm_supplied_workspace_argument(tmp_path: Path) -> None:
    class WorkspaceCheckingTool:
        name = "workspace_check"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201
            assert kwargs["workspace"] == tmp_path
            return ToolResult(tool_name=self.name, ok=True, summary="workspace ok")

    class WorkspacePollutingLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-workspace",
                            name="workspace_check",
                            args={"workspace": "/tmp/evil", "path": "."},
                        )
                    ]
                )
            return LLMResult(content="ok")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="参数污染", cwd=tmp_path)

    result = ReActLoop(WorkspacePollutingLLM(), ToolRegistry(tools=[WorkspaceCheckingTool()])).run(task, session)

    assert result == "ok"
    llm_events = [event for event in task.trace_events if event.phase == "llm"]
    assert llm_events
    assert "workspace" not in llm_events[0].data["tool_calls"][0]["args"]


def test_react_loop_requires_confirmation_before_writing_file(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, runtime=AgentRuntime())

    first_turn = manager.handle_user_message("在工作目录下新建 helloworld.py 文件")

    assert first_turn.pending_confirmation is not None
    assert first_turn.active_task is not None
    assert first_turn.active_task.status == "waiting_confirmation"
    assert not (tmp_path / "helloworld.py").exists()

    second_turn = manager.handle_user_message("确认")

    assert second_turn.active_task is not None
    assert second_turn.active_task.status == "done"
    assert (tmp_path / "helloworld.py").read_text(encoding="utf-8") == "print('hello world')\n"
    assert second_turn.active_task.observations[-1].ok is True
    assert second_turn.active_task.observations[-1].summary == "wrote helloworld.py"


def test_dry_run_does_not_write_files(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, runtime=AgentRuntime(dry_run=True))

    session = manager.handle_user_message("在工作目录下新建 helloworld.py 文件")

    assert session.pending_confirmation is not None
    assert session.active_task is not None
    assert session.active_task.status == "waiting_confirmation"
    assert not (tmp_path / "helloworld.py").exists()
    assert session.active_task.observations[-1].summary.startswith("dry-run preview")
    assert any(event.data.get("dry_run") is True for event in session.active_task.trace_events)


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
    assert result.reason == "draft is sufficient"


def test_runtime_respects_engineering_step_limit(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(default_limits=LoopLimits(max_engineering_steps=1))

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    assert result.active_task.step_count == 1


def test_runtime_converts_agent_exception_to_failed_message(tmp_path: Path) -> None:
    class FailingReflectionLoop:
        def run(self, task: TaskState, session: SessionState):  # noqa: ARG002
            raise RuntimeError("LLM HTTP 400: bad request")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime()
    runtime.reflection_loop = FailingReflectionLoop()

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    assert result.active_task.status == "failed"
    assert result.active_task.errors
    assert "LLM HTTP 400" in result.messages[-1].content


def test_runtime_marks_react_iteration_limit_error_code(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(default_limits=LoopLimits(max_react_iterations=0))

    result = runtime.on_user_message("读取 a.md", session)

    assert result.active_task is not None
    assert result.active_task.status == "failed"
    assert result.active_task.errors[0].code == "MAX_REACT_ITERATIONS_REACHED"
    assert any(event.message == "Runtime caught execution error" for event in result.active_task.trace_events)


def test_runtime_marks_token_budget_exceeded_error_code(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("x" * 500))
    runtime = AgentRuntime(default_limits=LoopLimits(max_estimated_tokens=1))

    result = runtime.on_user_message("再补充一点", session)

    assert result.active_task is not None
    assert result.active_task.status == "failed"
    assert result.active_task.errors[0].code == "TOKEN_BUDGET_EXCEEDED"


def test_runtime_records_context_budget_usage_and_compression_trigger(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.messages.extend(Message.user("x" * 120) for _ in range(4))
    runtime = AgentRuntime(
        default_limits=LoopLimits(max_estimated_tokens=100),
        logger=EventLogger(tmp_path / "runs", enabled=True),
    )

    result = runtime.on_user_message("继续", session)

    assert result.active_task is not None
    log_path = tmp_path / "runs" / result.active_task.run_id / "events.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    budget_rows = [row for row in rows if row.get("type") == "context_budget"]
    assert budget_rows
    row = budget_rows[0]
    assert row["estimated_tokens"] > 0
    assert row["model_context_limit"] == 100
    assert row["context_usage"] >= 0.70
    assert row["compression_triggered"] is True


def test_runtime_exposes_active_task_before_reflection_runs(tmp_path: Path) -> None:
    class InspectingReflectionLoop:
        def run(self, task: TaskState, session: SessionState) -> ReflectionResult:
            assert session.active_task is task
            return ReflectionResult(accepted=True, content="ok", reason="accepted")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime()
    runtime.reflection_loop = InspectingReflectionLoop()

    result = runtime.on_user_message("写报告", session)

    assert result.active_task is not None
    assert result.active_task.result == "ok"


def test_runtime_falls_back_on_invalid_llm_output(tmp_path: Path) -> None:
    class BrokenLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            raise ValueError("malformed output")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime()
    runtime.react_loop.llm = BrokenLLM()

    result = runtime.on_user_message("写报告", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert result.messages[-1].content.startswith("已使用规则兜底生成草稿")
    assert any(
        event.phase == "llm" and "falling back to rule-based draft" in event.message
        for event in result.active_task.trace_events
    )


def test_runtime_fallback_summary_includes_recent_tool_observations(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# project\n", encoding="utf-8")

    class BrokenLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[ToolCall(id="call-read-readme", name="read_file", args={"path": "README.md"})]
                )
            raise ValueError("malformed output")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime()
    runtime.react_loop.llm = BrokenLLM()

    result = runtime.on_user_message("请检查项目", session)

    assert result.active_task is not None
    assert "最近工具结果" in result.messages[-1].content
    assert "read README.md" in result.messages[-1].content


def test_runtime_logs_llm_request_and_response_payloads(tmp_path: Path) -> None:
    runtime = AgentRuntime(logger=EventLogger(tmp_path / "runs", enabled=True))
    session = SessionState.create(cwd=tmp_path)

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    log_path = tmp_path / "runs" / result.active_task.run_id / "events.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    request_rows = [row for row in rows if row.get("type") == "llm_request"]
    response_rows = [row for row in rows if row.get("type") == "llm_response"]
    assert request_rows
    assert response_rows
    assert request_rows[0]["request"]["messages"]
    assert request_rows[0]["request"]["tool_names"]
    assert response_rows[0]["request"]["messages"]
    assert "response" in response_rows[0]


def test_runtime_stops_when_total_runtime_limit_is_exceeded(tmp_path: Path) -> None:
    class SlowReflectionLoop:
        def run(self, task: TaskState, session: SessionState) -> ReflectionResult:  # noqa: ARG002
            sleep(0.03)
            return ReflectionResult(accepted=True, content="too late", reason="accepted")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(default_limits=LoopLimits(max_runtime_seconds=0))
    runtime.reflection_loop = SlowReflectionLoop()

    result = runtime.on_user_message("写报告", session)

    assert result.active_task is not None
    assert result.active_task.status == "failed"
    assert result.active_task.errors[-1].code == "RUNTIME_TIMEOUT"
    assert "运行超时" in result.messages[-1].content


def test_runtime_report_redacts_secrets_from_process_and_observations(tmp_path: Path) -> None:
    class SecretReflectionLoop:
        def run(self, task: TaskState, session: SessionState) -> ReflectionResult:  # noqa: ARG002
            task.trace_events.append(
                TraceEvent(
                    phase="tool",
                    message="secret trace",
                    data={"api_key": "sk-live-secret", "note": "password=abc123"},
                )
            )
            task.observations.append(
                Observation(
                    ok=True,
                    summary="read env",
                    content="LLM_API_KEY=sk-live-secret\nnormal=value",
                    tool_call_id="call-secret",
                )
            )
            return ReflectionResult(accepted=True, content="final token sk-live-secret", reason="accepted")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(reporter=Reporter(tmp_path / "outputs"))
    runtime.reflection_loop = SecretReflectionLoop()

    result = runtime.on_user_message("我的 api_key 是 sk-live-secret", session)

    assert result.active_task is not None
    report = result.active_task.artifacts[-1].path.read_text(encoding="utf-8")
    assert "sk-live-secret" not in report
    assert "password=abc123" not in report
    assert "[REDACTED]" in report
