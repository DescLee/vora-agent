import json
from pathlib import Path
import re
import threading
from time import perf_counter, sleep

from manus_mini.llm import LLMResult
from manus_mini.context import estimate_message_tokens
from manus_mini.executor import Executor
from manus_mini.models import LoopLimits, Message, Observation, PlanStep, SessionState, TaskState, ToolCall, TraceEvent
from manus_mini.react import ReActLoop, format_tool_result_message
from manus_mini.reflection import ReflectionLoop, ReflectionResult
from manus_mini.logging import EventLogger
from manus_mini.reporter import Reporter
from manus_mini.logging import project_outputs_dir
from manus_mini.runtime import AgentRuntime
from manus_mini.session import SessionManager
from manus_mini.tools.base import ToolResult
from manus_mini.tools.file_tools import ReplaceInFileTool
from manus_mini.tools.registry import ToolRegistry
from support import ScriptedLLM


def session_event_log_path(root: Path, session_id: str) -> Path:
    return root / session_id / "node.jsonl"


def test_runtime_turns_user_request_into_agent_reply(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello world", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())

    result = runtime.on_user_message("读取 a.md", session)

    assert result.active_task is not None
    assert result.messages[-1].role == "agent"
    assert "hello world" in result.messages[-1].content
    assert any(message.role == "tool" for message in result.messages)
    assert any(observation.tool_call_id == "call-read-a" for observation in result.active_task.observations)
    assert any(event.phase == "llm" for event in result.active_task.trace_events)
    assert any(event.data.get("tool_name") == "read_file" for event in result.active_task.trace_events)


def test_runtime_persists_tool_history_into_session_messages(tmp_path: Path) -> None:
    class ToolThenAnswerLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    content="先读取 README。",
                    tool_calls=[ToolCall(id="call-read-readme", name="read_file", args={"path": "README.md"})],
                )
            return LLMResult(content="完成")

    (tmp_path / "README.md").write_text("hello world", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.react_loop.llm = ToolThenAnswerLLM()

    result = runtime.on_user_message("读取 README.md", session)

    assert result.active_task is not None
    roles = [message.role for message in result.messages]
    assert "agent" in roles
    assert "tool" in roles
    tool_messages = [message for message in result.messages if message.role == "tool" and message.tool_call_id == "call-read-readme"]
    assert len(tool_messages) == 1


def test_runtime_small_talk_does_not_call_file_tools(tmp_path: Path) -> None:
    class ChatLLM:
        def __init__(self) -> None:
            self.calls = 0
            self.tool_names = []

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            self.tool_names.append(list(tool_names))
            return LLMResult(content="LLM chat reply")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.react_loop.llm = ChatLLM()

    result = runtime.on_user_message("你好，今天状态怎么样？", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert result.active_task.plan[0].intent == "chat"
    assert runtime.react_loop.llm.calls >= 1
    assert runtime.react_loop.llm.tool_names[0] == []
    assert any(event.phase == "reflection" for event in result.active_task.trace_events)
    assert not [event for event in result.active_task.trace_events if event.phase == "tool"]
    assert result.messages[-1].content == "LLM chat reply"


def test_runtime_does_not_inject_failed_task_result_as_existing_artifact(tmp_path: Path) -> None:
    class ChatLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(content="可以继续。")

    session = SessionState.create(cwd=tmp_path)
    failed_task = TaskState.create(goal="创建文件", cwd=tmp_path)
    failed_task.status = "failed"
    failed_task.result = "用户拒绝了待确认写入。"
    session.active_task = failed_task
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.react_loop.llm = ChatLLM()

    result = runtime.on_user_message("你好", session)

    assert not [
        message
        for message in result.messages
        if message.role == "system" and "已有产物" in message.content and "用户拒绝" in message.content
    ]


def test_runtime_identity_question_uses_manus_mini_system_identity(tmp_path: Path) -> None:
    class IdentityLLM:
        def __init__(self) -> None:
            self.system_prompts = []
            self.tool_names = []

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.tool_names.append(list(tool_names))
            self.system_prompts.extend(message.content for message in messages if message.role == "system")
            return LLMResult(content="我叫 manus-mini，是你的个人助理。")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.react_loop.llm = IdentityLLM()

    result = runtime.on_user_message("你的名字是啥", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert result.active_task.plan[0].intent == "chat"
    assert runtime.react_loop.llm.tool_names[0] == []
    assert any("你叫 manus-mini" in prompt for prompt in runtime.react_loop.llm.system_prompts)
    assert any("个人助理" in prompt for prompt in runtime.react_loop.llm.system_prompts)
    assert "manus-mini" in result.messages[-1].content


def test_runtime_scripted_llm_answers_identity_question_with_manus_mini(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())

    result = runtime.on_user_message("你的名字是啥", session)

    assert result.active_task is not None
    assert result.active_task.plan[0].intent == "chat"
    assert "manus-mini" in result.messages[-1].content


def test_runtime_cli_usage_error_passes_through_reflection(tmp_path: Path) -> None:
    class CliIssueLLM:
        def __init__(self) -> None:
            self.calls = 0
            self.tool_names = []

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            self.tool_names.append(list(tool_names))
            return LLMResult(content="正确用法是 `manus-mini remove <session_id>`")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.react_loop.llm = CliIssueLLM()

    result = runtime.on_user_message("manus-mini list remove session-45dc2367524b 这个报错什么意思", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert result.active_task.plan
    assert all(step.intent != "chat" for step in result.active_task.plan)
    assert runtime.react_loop.llm.calls >= 1
    assert any(event.phase == "reflection" for event in result.active_task.trace_events)
    assert "remove <session_id>" in result.messages[-1].content or "正确用法" in result.messages[-1].content


def test_runtime_project_summary_uses_project_files(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# manus-mini\n\nTUI Agent runtime.", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = \"manus-mini\"\n", encoding="utf-8")
    (tmp_path / "docs" / "v1-technical-design.md").write_text("三层 Agent Loop、工具调度、长期记忆。", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())

    result = runtime.on_user_message("请你获取一下当前项目，并说明下它的作用", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert "manus-mini" in result.messages[-1].content
    assert "TUI" in result.messages[-1].content
    tool_events = [event for event in result.active_task.trace_events if event.phase == "tool"]
    assert any(event.data.get("tool_name") == "list_files" for event in tool_events)
    assert any(event.data.get("tool_name") == "read_file" for event in tool_events)
    assert runtime.memory_manager.search("manus-mini", limit=5)


def test_runtime_project_question_does_not_become_chat_only_when_planner_mislabels_intent(tmp_path: Path) -> None:
    class BadPlanner:
        def build_plan(self, goal, session):  # noqa: ANN001, ANN201, ARG002
            from manus_mini.models import PlanStep

            return [PlanStep(description="读取 README.md 了解项目概述", intent="chat")]

    class InspectingLLM:
        def __init__(self) -> None:
            self.tool_names = []
            self.system_prompts = []

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.tool_names.append(list(tool_names))
            self.system_prompts.extend(message.content for message in messages if message.role == "system")
            return LLMResult(content="这是当前工作目录里的项目。")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.planner = BadPlanner()
    runtime.react_loop.llm = InspectingLLM()

    result = runtime.on_user_message("这个项目是做什么的，简单的说，越简单越好", session)

    assert result.active_task is not None
    assert runtime.react_loop.llm.tool_names[0]
    assert any("用户说“当前项目”“这个项目”“这个工程”时，指的就是当前工作目录" in prompt for prompt in runtime.react_loop.llm.system_prompts)
    assert any("先定位目标模块和最小相关文件" in prompt for prompt in runtime.react_loop.llm.system_prompts)
    assert any("没有读取原文件前，不要凭空改写已有文件" in prompt for prompt in runtime.react_loop.llm.system_prompts)
    assert any("不要只给选项并等待用户选择" in prompt for prompt in runtime.react_loop.llm.system_prompts)
    assert any("最终答复要说明改了什么、验证了什么" in prompt for prompt in runtime.react_loop.llm.system_prompts)
    assert "当前工作目录里的项目" in result.messages[-1].content


def test_runtime_sends_project_code_overview_to_planner_before_project_requests(tmp_path: Path) -> None:
    class OverviewAwareLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                assert tool_names == []
                system_messages = [message for message in messages if message.role == "system"]
                assert any("你叫 manus-mini" in message.content for message in system_messages)
                assert any("项目代码目录结构" in message.content for message in system_messages)
                assert any("src/：核心实现代码" in message.content for message in system_messages)
                assert any("工具使用要克制" in message.content for message in system_messages)
                return LLMResult(content="1. 基于目录结构判断关键文件 | research")
            return LLMResult(content="已了解目录结构，先看 README。")

    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "manus_mini").mkdir(parents=True)
    (tmp_path / "src" / "manus_mini" / "runtime.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "design.md").write_text("design", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_runtime.py").write_text("def test_x(): pass", encoding="utf-8")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=OverviewAwareLLM())

    result = runtime.on_user_message("请你看下当前项目代码结构，先帮我判断应该看哪些文件", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert result.messages[-1].content == "已了解目录结构，先看 README。"


def test_runtime_output_file_records_input_process_observations_and_result_in_chunks(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello world", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(
        reporter=Reporter(tmp_path / "outputs"),
        logger=EventLogger(tmp_path / "logs", enabled=True),
        llm=ScriptedLLM(),
    )

    result = runtime.on_user_message("读取 a.md", session)

    assert result.active_task is not None
    artifact_path = result.active_task.artifacts[-1].path
    content = artifact_path.read_text(encoding="utf-8")

    assert "# Manus Mini Run" in content
    assert "## 1. 用户目标" in content
    assert "读取 a.md" in content
    assert "## 2. 执行步骤" in content
    assert "### 2.1" in content
    assert "ReAct iteration" in content
    assert "## 3. 工具调用" in content
    assert "call-read-a" in content
    assert "hello world" in content
    assert "## 4. 输出产物" in content
    assert result.messages[-1].content in content
    log_dir = tmp_path / "logs" / result.session_id
    assert sorted(path.name for path in log_dir.iterdir()) == ["node.jsonl", "pipeline.jsonl", "summary.jsonl"]
    summary_rows = [json.loads(line) for line in (log_dir / "summary.jsonl").read_text(encoding="utf-8").splitlines()]
    pipeline_rows = [json.loads(line) for line in (log_dir / "pipeline.jsonl").read_text(encoding="utf-8").splitlines()]
    node_rows = [json.loads(line) for line in (log_dir / "node.jsonl").read_text(encoding="utf-8").splitlines()]
    assert summary_rows[-1]["user_input"] == "读取 a.md"
    assert summary_rows[-1]["result"] == result.messages[-1].content
    final_node_id = summary_rows[-1]["final_node_id"]
    assert any(row["node_id"] == final_node_id for row in pipeline_rows)
    assert any(row["node_id"] == final_node_id for row in node_rows)


def test_runtime_output_filename_starts_with_timestamp_for_lookup(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(reporter=Reporter(tmp_path / "outputs"), llm=ScriptedLLM())

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    filename = result.active_task.artifacts[-1].path.name
    assert re.match(r"^\d{8}-\d{6}-run-[a-f0-9]{12}\.md$", filename)


def test_runtime_defaults_to_project_reporter_output_dir_under_pytest(tmp_path: Path) -> None:
    runtime = AgentRuntime(llm=ScriptedLLM(), cwd=tmp_path)

    assert runtime.reporter.output_dir == project_outputs_dir(tmp_path)
    assert ".manus-mini/projects/" in str(runtime.logger.root)
    assert str(runtime.logger.root).endswith("/logs")
    assert runtime.reporter.run_root is not None
    assert runtime.reporter.run_root == project_outputs_dir(tmp_path).parent / "logs"

    session = SessionState.create(cwd=tmp_path)
    runtime.on_user_message("你好", session)

    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "logs").exists()
    assert not (tmp_path / "outputs").exists()


def test_react_loop_forces_final_answer_when_budget_is_zero(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="循环测试", cwd=tmp_path, limits=LoopLimits(max_react_iterations=0))

    result = ReActLoop(ScriptedLLM(), ToolRegistry()).run(task, session)

    assert result == "报告草稿：循环测试"
    assert any(
        event.phase == "react" and "forcing final answer" in event.message
        for event in task.trace_events
    )


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


def test_react_loop_records_llm_usage_from_source_response(tmp_path: Path) -> None:
    class UsageLLM:
        def context_limit(self) -> int | None:
            return 1_000

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(
                content="done",
                source_response={
                    "usage": {
                        "prompt_tokens": 321,
                        "completion_tokens": 45,
                        "total_tokens": 366,
                    }
                },
            )

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="记录用量", cwd=tmp_path)

    result = ReActLoop(UsageLLM(), ToolRegistry()).run(task, session)

    assert result == "done"
    assert task.last_prompt_tokens == 321
    assert task.last_completion_tokens == 45
    assert task.last_total_tokens == 366


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


def test_react_loop_rejects_tool_calls_beyond_iteration_budget(tmp_path: Path) -> None:
    class CountingTool:
        name = "counting"
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
            return ToolResult(tool_name=self.name, ok=True, summary="counted")

    class TooManyToolsLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(id="call-1", name="counting"),
                        ToolCall(id="call-2", name="counting"),
                        ToolCall(id="call-3", name="counting"),
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert len(tool_messages) == 3
            assert "TOOL_CALL_BUDGET_EXCEEDED" in tool_messages[-1].content
            return LLMResult(content="budget handled")

    tool = CountingTool()
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(
        goal="预算测试",
        cwd=tmp_path,
        limits=LoopLimits(max_tool_calls_per_iteration=2),
    )

    result = ReActLoop(TooManyToolsLLM(), ToolRegistry(tools=[tool])).run(task, session)

    assert result == "budget handled"
    assert tool.calls == 2
    assert task.observations[-1].summary == "tool call rejected by iteration budget"


def test_react_loop_rejects_code_write_before_test_case_runs(tmp_path: Path) -> None:
    class EditBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                        tool_calls=[
                            ToolCall(
                                id="call-replace",
                                name="replace_in_file",
                                args={"path": "app.py", "old_text": "old", "new_text": "new", "confirmed": True},
                            )
                        ]
                    )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    (tmp_path / "app.py").write_text("old", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改 app.py 代码", cwd=tmp_path)

    result = ReActLoop(EditBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "old"
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_react_loop_rejects_shell_code_write_before_test_case_runs(tmp_path: Path) -> None:
    class ShellEditBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-write",
                            name="run_bash",
                            args={"command": "cat <<'EOF' > app.py\nnew\nEOF", "confirmed": True},
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改 app.py 代码", cwd=tmp_path)

    result = ReActLoop(ShellEditBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "old\n"
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_react_loop_rejects_shell_tee_code_write_before_test_case_runs(tmp_path: Path) -> None:
    class ShellTeeEditBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-tee-write",
                            name="run_bash",
                            args={"command": "printf 'new\\n' | tee app.py >/dev/null", "confirmed": True},
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改 app.py 代码", cwd=tmp_path)

    result = ReActLoop(ShellTeeEditBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "old\n"
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_react_loop_rejects_shell_in_place_code_write_before_test_case_runs(tmp_path: Path) -> None:
    class ShellInPlaceEditBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-sed-write",
                            name="run_bash",
                            args={"command": "sed -i '' 's/^old$/new/' app.py", "confirmed": True},
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改 app.py 代码", cwd=tmp_path)

    result = ReActLoop(ShellInPlaceEditBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "old\n"
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_react_loop_rejects_shell_python_code_write_before_test_case_runs(tmp_path: Path) -> None:
    class ShellPythonEditBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-python-write",
                            name="run_bash",
                            args={
                                "command": "python -c \"from pathlib import Path; Path('app.py').write_text('new\\n')\"",
                                "confirmed": True,
                            },
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改 app.py 代码", cwd=tmp_path)

    result = ReActLoop(ShellPythonEditBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "old\n"
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_react_loop_rejects_shell_python_path_open_code_write_before_test_case_runs(tmp_path: Path) -> None:
    class ShellPythonPathOpenEditBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-python-path-open-write",
                            name="run_bash",
                            args={
                                "command": (
                                    "python -c \"from pathlib import Path; "
                                    "Path('app.py').open('w').write('new\\\\n')\""
                                ),
                                "confirmed": True,
                            },
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改 app.py 代码", cwd=tmp_path)

    result = ReActLoop(ShellPythonPathOpenEditBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "old\n"
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_react_loop_rejects_shell_python_write_bytes_code_write_before_test_case_runs(tmp_path: Path) -> None:
    class ShellPythonWriteBytesEditBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-python-write-bytes",
                            name="run_bash",
                            args={
                                "command": (
                                    "python -c \"from pathlib import Path; "
                                    "Path('app.py').write_bytes(b'new\\\\n')\""
                                ),
                                "confirmed": True,
                            },
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改 app.py 代码", cwd=tmp_path)

    result = ReActLoop(ShellPythonWriteBytesEditBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "old\n"
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_react_loop_rejects_shell_touch_code_file_before_test_case_runs(tmp_path: Path) -> None:
    class ShellTouchCodeFileBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-touch-code-file",
                            name="run_bash",
                            args={"command": "touch app.py", "confirmed": True},
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="创建 app.py 并写实现", cwd=tmp_path)

    result = ReActLoop(ShellTouchCodeFileBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert not (tmp_path / "app.py").exists()
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_react_loop_rejects_shell_touch_code_file_with_options_before_test_case_runs(tmp_path: Path) -> None:
    class ShellTouchCodeFileWithOptionsBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-touch-code-file-with-options",
                            name="run_bash",
                            args={"command": "touch -c app.py", "confirmed": True},
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="创建 app.py 并写实现", cwd=tmp_path)

    result = ReActLoop(ShellTouchCodeFileWithOptionsBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert not (tmp_path / "app.py").exists()
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_react_loop_rejects_shell_touch_code_file_after_test_path_before_test_case_runs(tmp_path: Path) -> None:
    class ShellTouchCodeFileAfterTestPathBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-touch-code-file-after-test-path",
                            name="run_bash",
                            args={"command": "touch tests/test_app.py app.py", "confirmed": True},
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="创建 app.py 并写实现", cwd=tmp_path)

    result = ReActLoop(ShellTouchCodeFileAfterTestPathBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert not (tmp_path / "app.py").exists()
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_react_loop_rejects_shell_python_touch_code_file_before_test_case_runs(tmp_path: Path) -> None:
    class ShellPythonTouchCodeFileBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-python-touch-code-file",
                            name="run_bash",
                            args={
                                "command": "python -c \"from pathlib import Path; Path('app.py').touch()\"",
                                "confirmed": True,
                            },
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="创建 app.py 并写实现", cwd=tmp_path)

    result = ReActLoop(ShellPythonTouchCodeFileBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert not (tmp_path / "app.py").exists()
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_run_bash_touch_file_requires_confirmation(tmp_path: Path) -> None:
    class ShellTouchLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-touch-write",
                        name="run_bash",
                        args={"command": "touch note.md"},
                    )
                ]
            )

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="创建 note.md", cwd=tmp_path)

    result = ReActLoop(ShellTouchLLM(), ToolRegistry()).run(task, session)

    assert result == "即将执行: command modifies workspace files: touch"
    assert session.pending_confirmation is not None
    assert session.pending_confirmation.tool_name == "run_bash"
    assert "command modifies workspace files: touch" in session.pending_confirmation.summary
    assert not (tmp_path / "note.md").exists()


def test_run_bash_pathlib_touch_file_requires_confirmation(tmp_path: Path) -> None:
    class ShellPathlibTouchLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-pathlib-touch-write",
                        name="run_bash",
                        args={"command": "python -c \"from pathlib import Path; Path('note.md').touch()\""},
                    )
                ]
            )

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="创建 note.md", cwd=tmp_path)

    result = ReActLoop(ShellPathlibTouchLLM(), ToolRegistry()).run(task, session)

    assert result == "即将执行: command modifies workspace files: touch"
    assert session.pending_confirmation is not None
    assert session.pending_confirmation.tool_name == "run_bash"
    assert "command modifies workspace files: touch" in session.pending_confirmation.summary
    assert not (tmp_path / "note.md").exists()


def test_react_loop_rejects_compound_shell_code_write_after_test_file_write(tmp_path: Path) -> None:
    class CompoundShellEditBeforeTestLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-compound-write",
                            name="run_bash",
                            args={
                                "command": (
                                    "cat <<'EOF' > tests/test_app.py\n"
                                    "def test_app():\n"
                                    "    assert True\n"
                                    "EOF\n"
                                    "cat <<'EOF' > app.py\n"
                                    "new\n"
                                    "EOF"
                                ),
                                "confirmed": True,
                            },
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "CODE_CHANGE_REQUIRES_TEST_FIRST" in tool_messages[-1].content
            return LLMResult(content="先补测试再改代码")

    (tmp_path / "app.py").write_text("old\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改 app.py 代码", cwd=tmp_path)

    result = ReActLoop(CompoundShellEditBeforeTestLLM(), ToolRegistry()).run(task, session)

    assert result == "先补测试再改代码"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "old\n"
    assert task.observations[-1].summary == "code change rejected: run a test case before editing production code"


def test_react_loop_allows_five_read_files_in_one_iteration(tmp_path: Path) -> None:
    class FiveReadFilesLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(id=f"call-read-{index}", name="read_file", args={"path": f"file-{index}.txt"})
                        for index in range(5)
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert len(tool_messages) == 5
            assert all("TOOL_CALL_BUDGET_EXCEEDED" not in message.content for message in tool_messages)
            return LLMResult(content="all reads handled")

    for index in range(5):
        (tmp_path / f"file-{index}.txt").write_text(f"content {index}", encoding="utf-8")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(
        goal="读取多个文件",
        cwd=tmp_path,
        limits=LoopLimits(max_tool_calls_per_iteration=5),
    )

    result = ReActLoop(FiveReadFilesLLM(), ToolRegistry()).run(task, session)

    assert result == "all reads handled"
    assert len(task.observations) == 5
    assert all(observation.ok for observation in task.observations)


def test_react_loop_allows_overview_task_to_read_targeted_source_files(tmp_path: Path) -> None:
    class OverviewLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(id="call-read-readme", name="read_file", args={"path": "README.md"}),
                        ToolCall(id="call-read-deep", name="read_file", args={"path": "src/feature/deep.py"}),
                    ]
                )
            tool_messages = {message.tool_call_id: message.content for message in messages if message.role == "tool"}
            assert "# demo" in tool_messages["call-read-readme"]
            assert "SECRET = 'can be read when targeted'" in tool_messages["call-read-deep"]
            return LLMResult(content="overview handled")

    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    (tmp_path / "src" / "feature").mkdir(parents=True)
    (tmp_path / "src" / "feature" / "deep.py").write_text("SECRET = 'can be read when targeted'", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="请你看下这个项目提一些优化建议", cwd=tmp_path)

    result = ReActLoop(OverviewLLM(), ToolRegistry()).run(task, session)

    assert result == "overview handled"
    deep_reads = [observation for observation in task.observations if observation.tool_call_id == "call-read-deep"]
    assert deep_reads
    assert deep_reads[0].ok is True
    assert "SECRET = 'can be read when targeted'" in deep_reads[0].content


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


def test_react_loop_skips_duplicate_successful_read_file_calls(tmp_path: Path) -> None:
    class RepeatingReadLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-read-1", name="read_file", args={"path": "README.md"})])
            if self.calls == 2:
                return LLMResult(tool_calls=[ToolCall(id="call-read-2", name="read_file", args={"path": "README.md"})])
            return LLMResult(content="ok")

    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="重复读取 README", cwd=tmp_path)

    result = ReActLoop(RepeatingReadLLM(), ToolRegistry()).run(task, session)

    assert result == "ok"
    read_events = [
        event for event in task.trace_events
        if event.phase == "tool" and event.data.get("tool_name") == "read_file"
    ]
    assert [event.data.get("summary") for event in read_events] == [
        "read README.md",
        "read_file skipped: already read README.md",
    ]
    assert read_events[1].data.get("deduplicated") is True
    assert task.observations[-1].content == "# demo"


def test_react_loop_skips_duplicate_read_file_calls_in_same_iteration(tmp_path: Path) -> None:
    class DuplicateReadInSameIterationLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(id="call-read-1", name="read_file", args={"path": "README.md"}),
                        ToolCall(id="call-read-2", name="read_file", args={"path": "README.md"}),
                    ]
                )
            return LLMResult(content="ok")

    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="重复读取 README", cwd=tmp_path)

    result = ReActLoop(DuplicateReadInSameIterationLLM(), ToolRegistry()).run(task, session)

    assert result == "ok"
    read_events = [
        event for event in task.trace_events
        if event.phase == "tool" and event.data.get("tool_name") == "read_file"
    ]
    assert [event.data.get("summary") for event in read_events] == [
        "read README.md",
        "read_file skipped: duplicate request in current iteration README.md",
    ]
    assert read_events[1].data.get("deduplicated") is True


def test_react_loop_skips_duplicate_list_files_calls_across_iterations(tmp_path: Path) -> None:
    class RepeatingListLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-list-1", name="list_files", args={"path": "."})])
            if self.calls == 2:
                return LLMResult(tool_calls=[ToolCall(id="call-list-2", name="list_files", args={"path": "."})])
            return LLMResult(content="ok")

    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="重复列目录", cwd=tmp_path)

    result = ReActLoop(RepeatingListLLM(), ToolRegistry()).run(task, session)

    assert result == "ok"
    list_events = [
        event for event in task.trace_events
        if event.phase == "tool" and event.data.get("tool_name") == "list_files"
    ]
    assert list_events[0].data.get("summary") == "found 1 files"
    assert list_events[1].data.get("summary") == "list_files skipped: duplicate request already completed"
    assert list_events[1].data.get("deduplicated") is True


def test_react_loop_blocks_duplicate_command_calls_across_iterations(tmp_path: Path) -> None:
    class RepeatingCommandLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-bash-1", name="run_bash", args={"command": "printf x"})])
            if self.calls == 2:
                return LLMResult(tool_calls=[ToolCall(id="call-bash-2", name="run_bash", args={"command": "printf x"})])
            return LLMResult(content="ok")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="重复执行命令", cwd=tmp_path)

    result = ReActLoop(RepeatingCommandLLM(), ToolRegistry()).run(task, session)

    assert result == "ok"
    command_events = [
        event for event in task.trace_events
        if event.phase == "tool" and event.data.get("tool_name") == "run_bash"
    ]
    assert [event.data.get("error_code") for event in command_events] == [None, "DUPLICATE_TOOL_CALL_BLOCKED"]
    assert command_events[1].data.get("blocked_duplicate") is True


def test_react_loop_requires_confirmation_when_llm_marks_command_high_risk(tmp_path: Path) -> None:
    external_path = tmp_path.parent / "outside-marker.txt"

    class ExternalCommandLLM:
        supports_command_risk_judgement = True

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            if any("classify shell command risk" in getattr(message, "content", "") for message in messages):
                return LLMResult(content='{"risk_level":"high","reason":"deletes a user-selected file"}')
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-rm-outside",
                        name="run_bash",
                        args={"command": f"rm -f {external_path}"},
                    )
                ]
            )

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="删除外部文件", cwd=tmp_path)

    result = ReActLoop(ExternalCommandLLM(), ToolRegistry()).run(task, session)

    assert "确认" in result or "即将执行" in result
    assert session.pending_confirmation is not None
    assert session.pending_confirmation.tool_name == "run_bash"
    assert "LLM marked command as high risk" in session.pending_confirmation.summary
    assert not external_path.exists()


def test_run_bash_in_place_file_edit_requires_confirmation(tmp_path: Path) -> None:
    class SedEditLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-sed-edit",
                        name="run_bash",
                        args={"command": "sed -i '' 's/^old line$/new line/' note.md"},
                    )
                ]
            )

    (tmp_path / "note.md").write_text("# Note\n\nold line\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="请把 note.md 里的 old line 改成 new line。", cwd=tmp_path)

    result = ReActLoop(SedEditLLM(), ToolRegistry()).run(task, session)

    assert "确认" in result or "即将执行" in result
    assert session.pending_confirmation is not None
    assert session.pending_confirmation.tool_name == "run_bash"
    assert "modifies workspace files" in session.pending_confirmation.summary
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == "# Note\n\nold line\n"


def test_run_bash_redirected_file_write_requires_confirmation(tmp_path: Path) -> None:
    class RedirectWriteLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-cat-write",
                        name="run_bash",
                        args={"command": "cat <<'EOF' > note.md\nnew line\nEOF"},
                    )
                ]
            )

    (tmp_path / "note.md").write_text("old line\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="请把 note.md 改成 new line。", cwd=tmp_path)

    result = ReActLoop(RedirectWriteLLM(), ToolRegistry()).run(task, session)

    assert "确认" in result or "即将执行" in result
    assert session.pending_confirmation is not None
    assert session.pending_confirmation.tool_name == "run_bash"
    assert "modifies workspace files" in session.pending_confirmation.summary
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == "old line\n"


def test_run_bash_tee_file_write_requires_confirmation(tmp_path: Path) -> None:
    class TeeWriteLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-tee-write",
                        name="run_bash",
                        args={"command": "printf 'new line\\n' | tee note.md >/dev/null"},
                    )
                ]
            )

    (tmp_path / "note.md").write_text("old line\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="请把 note.md 改成 new line。", cwd=tmp_path)

    result = ReActLoop(TeeWriteLLM(), ToolRegistry()).run(task, session)

    assert "确认" in result or "即将执行" in result
    assert session.pending_confirmation is not None
    assert session.pending_confirmation.tool_name == "run_bash"
    assert "modifies workspace files" in session.pending_confirmation.summary
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == "old line\n"


def test_react_loop_rewrites_missing_root_source_path_to_unique_src_file(tmp_path: Path) -> None:
    class RootPathLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-read",
                            name="read_file",
                            args={"path": str(tmp_path / "prompt_tui_formatting.py")},
                        )
                    ]
                )
            return LLMResult(content="ok")

    source = tmp_path / "src" / "manus_mini" / "prompt_tui_formatting.py"
    source.parent.mkdir(parents=True)
    source.write_text("def format_status(): pass", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改 TUI 状态栏", cwd=tmp_path)

    result = ReActLoop(RootPathLLM(), ToolRegistry()).run(task, session)

    assert result == "ok"
    read_event = next(event for event in task.trace_events if event.phase == "tool" and event.data.get("tool_name") == "read_file")
    assert read_event.data["ok"] is True
    assert read_event.data["args"]["path"] == "src/manus_mini/prompt_tui_formatting.py"
    assert read_event.data.get("path_rewritten") is True


def test_react_loop_path_rewrite_ignores_noise_directories(tmp_path: Path) -> None:
    class RootPathLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-read",
                            name="read_file",
                            args={"path": "target.py"},
                        )
                    ]
                )
            return LLMResult(content="ok")

    source = tmp_path / "src" / "target.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('src')", encoding="utf-8")
    noisy = tmp_path / "node_modules" / "pkg" / "target.py"
    noisy.parent.mkdir(parents=True)
    noisy.write_text("print('dependency')", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="读取目标文件", cwd=tmp_path)

    result = ReActLoop(RootPathLLM(), ToolRegistry()).run(task, session)

    assert result == "ok"
    read_event = next(event for event in task.trace_events if event.phase == "tool" and event.data.get("tool_name") == "read_file")
    assert read_event.data["ok"] is True
    assert read_event.data["args"]["path"] == "src/target.py"


def test_react_loop_forces_final_answer_after_code_change_and_passing_test(tmp_path: Path) -> None:
    class EditThenKeepWorkingLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-test-before",
                            name="run_bash",
                            args={"command": "python -m pytest tests/test_app.py -q", "timeout_seconds": 30},
                        )
                    ]
                )
            if self.calls == 2:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-replace",
                            name="replace_in_file",
                            args={"path": "app.py", "old_text": "old", "new_text": "new", "confirmed": True},
                        )
                    ]
                )
            if self.calls == 3:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-test",
                            name="run_bash",
                            args={"command": "python -m pytest tests/test_app.py -q", "timeout_seconds": 30},
                        )
                    ]
                )
            if self.calls == 4:
                return LLMResult(tool_calls=[ToolCall(id="call-list", name="list_files", args={"path": "."})])
            return LLMResult(content="done")

    (tmp_path / "app.py").write_text("old", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text("def test_app():\n    assert True\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改 app.py 代码", cwd=tmp_path)
    llm = EditThenKeepWorkingLLM()

    result = ReActLoop(llm, ToolRegistry()).run(task, session)

    assert "测试" in result or "验证" in result or "完成" in result
    assert llm.calls == 4
    list_events = [event for event in task.trace_events if event.phase == "tool" and event.data.get("tool_name") == "list_files"]
    assert list_events == []
    assert any(event.message == "Code change validated; forcing final answer" for event in task.trace_events)


def test_react_loop_does_not_validate_code_change_when_test_output_contains_failures(tmp_path: Path) -> None:
    class FailingPipeRunBashTool:
        name = "run_bash"
        risk_level = "command"
        requires_confirmation = False
        is_read_only = False

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201
            return ToolResult(
                tool_name="run_bash",
                ok=True,
                summary="command exited 0",
                data={
                    "exit_code": 0,
                    "stdout": "================ FAILURES ================\nFAILED tests/test_app.py::test_app",
                    "stderr": "",
                },
            )

    class EditTestThenAnswerLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-test-before", name="run_bash", args={"command": "python -m pytest tests/test_app.py -q | tail -40"})])
            if self.calls == 2:
                return LLMResult(tool_calls=[ToolCall(id="call-replace", name="replace_in_file", args={"path": "app.py", "old_text": "old", "new_text": "new", "confirmed": True})])
            if self.calls == 3:
                return LLMResult(tool_calls=[ToolCall(id="call-test", name="run_bash", args={"command": "python -m pytest tests/test_app.py -q | tail -40"})])
            return LLMResult(content="测试失败，继续修复。")

    (tmp_path / "app.py").write_text("old", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text("def test_app():\n    assert True\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改 app.py 代码", cwd=tmp_path)
    registry = ToolRegistry(tools=[ReplaceInFileTool(), FailingPipeRunBashTool()])
    llm = EditTestThenAnswerLLM()

    result = ReActLoop(llm, registry).run(task, session)

    assert result == "测试失败，继续修复。"
    assert llm.calls == 4
    assert not any(event.message == "Code change validated; forcing final answer" for event in task.trace_events)


def test_react_loop_limits_fragmented_read_file_calls_in_one_iteration(tmp_path: Path) -> None:
    class ManySliceReadsLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(id=f"call-read-{index}", name="read_file", args={"path": "big.txt", "start_index": index * 100, "max_bytes": 100})
                        for index in range(5)
                    ]
                )
            return LLMResult(content="ok")

    (tmp_path / "big.txt").write_text("x" * 1000, encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="读取文件片段", cwd=tmp_path)

    result = ReActLoop(ManySliceReadsLLM(), ToolRegistry()).run(task, session)

    assert result == "ok"
    read_events = [
        event for event in task.trace_events
        if event.phase == "tool" and event.data.get("tool_name") == "read_file" and "ok" in event.data
    ]
    assert [event.data.get("ok") for event in read_events] == [True, True, False, False, False]
    assert all(event.data.get("error_code") == "READ_FILE_FRAGMENT_LIMIT_EXCEEDED" for event in read_events[2:])


def test_react_loop_allows_read_file_retry_after_file_too_large(tmp_path: Path) -> None:
    class RetryReadLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-small", name="read_file", args={"path": "README.md", "max_bytes": 1})])
            if self.calls == 2:
                return LLMResult(tool_calls=[ToolCall(id="call-large", name="read_file", args={"path": "README.md", "max_bytes": 20})])
            return LLMResult(content="ok")

    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="读取 README", cwd=tmp_path)

    result = ReActLoop(RetryReadLLM(), ToolRegistry()).run(task, session)

    assert result == "ok"
    read_events = [
        event for event in task.trace_events
        if event.phase == "tool" and event.data.get("tool_name") == "read_file"
    ]
    assert [event.data.get("error_code") for event in read_events] == ["FILE_TOO_LARGE", None]
    assert read_events[1].data.get("summary") == "read README.md"


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


def test_react_loop_does_not_retry_deterministic_tool_errors(tmp_path: Path) -> None:
    class TooLargeTool:
        name = "too_large"
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
            return ToolResult(tool_name=self.name, ok=False, summary="file too large", error_code="FILE_TOO_LARGE")

    class OneShotLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-too-large", name="too_large")])
            return LLMResult(content="done")

    tool = TooLargeTool()
    registry = ToolRegistry(tools=[tool])
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="读取大文件", cwd=tmp_path)

    result = ReActLoop(OneShotLLM(), registry).run(task, session)

    assert result == "done"
    assert tool.calls == 1
    assert not any(event.message == "Tool retry scheduled" for event in task.trace_events)


def test_executor_runs_tools_in_shared_thread_pool_capped_at_eight(tmp_path: Path) -> None:
    class ConcurrentTool:
        name = "concurrent"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            sleep(0.05)
            with self.lock:
                self.active -= 1
            return ToolResult(tool_name=self.name, ok=True, summary=f"done {kwargs['index']}")

    tool = ConcurrentTool()
    executor = Executor(ToolRegistry(tools=[tool]))
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="并发工具", cwd=tmp_path)
    calls = [
        ToolCall(id=f"call-{index}", name="concurrent", args={"workspace": tmp_path, "index": index})
        for index in range(12)
    ]

    results = executor.run_batch(calls, session, task)

    assert len(results) == 12
    assert all(result.ok for result in results.values())
    assert tool.max_active > 1
    assert tool.max_active <= 8


def test_executor_shutdown_can_detach_running_tool_threads_from_python_exit_join(tmp_path: Path) -> None:
    import concurrent.futures.thread as futures_thread

    class BlockingTool:
        name = "blocking"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201
            kwargs["started"].set()
            kwargs["release"].wait(timeout=5)
            return ToolResult(tool_name=self.name, ok=True, summary="done")

    started = threading.Event()
    release = threading.Event()
    executor = Executor(ToolRegistry(tools=[BlockingTool()]))
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="阻塞工具", cwd=tmp_path)
    future = executor._tool_pool.submit(
        executor._execute_sync,
        ToolCall(
            id="call-blocking",
            name="blocking",
            args={"workspace": tmp_path, "started": started, "release": release},
        ),
        session,
        task,
    )
    assert started.wait(timeout=1)
    worker_threads = list(executor._tool_pool._threads)
    assert worker_threads
    assert any(thread in futures_thread._threads_queues for thread in worker_threads)

    executor.shutdown(detach=True)

    assert all(thread not in futures_thread._threads_queues for thread in worker_threads)
    release.set()
    assert future.result(timeout=1).ok is True


def test_executor_threading_exit_hook_detaches_live_tool_threads(tmp_path: Path) -> None:
    import concurrent.futures.thread as futures_thread
    from manus_mini import executor as executor_module

    class BlockingTool:
        name = "blocking_exit"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201
            kwargs["started"].set()
            kwargs["release"].wait(timeout=5)
            return ToolResult(tool_name=self.name, ok=True, summary="done")

    started = threading.Event()
    release = threading.Event()
    executor = Executor(ToolRegistry(tools=[BlockingTool()]))
    future = executor._tool_pool.submit(
        executor._execute_sync,
        ToolCall(
            id="call-blocking-exit",
            name="blocking_exit",
            args={"workspace": tmp_path, "started": started, "release": release},
        ),
        SessionState.create(cwd=tmp_path),
        TaskState.create(goal="阻塞工具", cwd=tmp_path),
    )
    assert started.wait(timeout=1)
    worker_threads = list(executor._tool_pool._threads)
    assert any(thread in futures_thread._threads_queues for thread in worker_threads)

    executor_module._detach_live_tool_threads_from_python_shutdown()

    assert all(thread not in futures_thread._threads_queues for thread in worker_threads)
    release.set()
    assert future.result(timeout=1).ok is True
    executor.shutdown(detach=True)


def test_react_loop_does_not_time_out_tool_execution(tmp_path: Path) -> None:
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
    task = TaskState.create(goal="工具不限制执行时间", cwd=tmp_path, limits=LoopLimits(max_tool_timeout_seconds=0))

    result = ReActLoop(TimeoutLLM(), ToolRegistry(tools=[SlowTool()])).run(task, session)

    assert result == "timeout handled"
    assert task.observations[-1].ok is True
    assert task.observations[-1].summary == "done"


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
    task = TaskState.create(
        goal="重试耗尽",
        cwd=tmp_path,
        limits=LoopLimits(max_tool_retries=1, tool_retry_backoff_seconds=0),
    )

    result = ReActLoop(RetryExhaustedLLM(), ToolRegistry(tools=[AlwaysFailTool()])).run(task, session)

    assert result == "fallback"
    assert task.observations[-1].ok is False
    assert task.observations[-1].summary == "still failing"
    retry_events = [event for event in task.trace_events if event.message == "Tool retry scheduled"]
    assert retry_events[-1].data["delay_seconds"] == 0


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
    manager = SessionManager(tmp_path, runtime=AgentRuntime(llm=ScriptedLLM()))

    first_turn = manager.handle_user_message("在工作目录下新建 helloworld.py 文件")

    assert first_turn.pending_confirmation is not None
    assert first_turn.active_task is not None
    assert first_turn.active_task.status == "waiting_confirmation"
    assert not (tmp_path / "helloworld.py").exists()

    second_turn = manager.handle_user_message("确认")

    assert second_turn.active_task is not None
    assert second_turn.active_task.status == "done"
    assert (tmp_path / "helloworld.py").read_text(encoding="utf-8") == "print('hello world')\n"
    assert any(observation.ok and observation.summary == "wrote helloworld.py" for observation in second_turn.active_task.observations)
    assert any(observation.ok and observation.summary == "command exited 0" for observation in second_turn.active_task.observations)


def test_react_loop_requires_confirmation_before_replacing_file(tmp_path: Path) -> None:
    class ReplaceLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-replace-note",
                        name="replace_in_file",
                        args={"path": "note.md", "old_text": "old line", "new_text": "new line"},
                    )
                ]
            )

    (tmp_path / "note.md").write_text("# Note\n\nold line\n", encoding="utf-8")
    manager = SessionManager(tmp_path, runtime=AgentRuntime(llm=ReplaceLLM()))

    first_turn = manager.handle_user_message("请把 note.md 里的 old line 改成 new line")

    assert first_turn.pending_confirmation is not None
    assert first_turn.pending_confirmation.tool_name == "replace_in_file"
    assert first_turn.active_task is not None
    assert first_turn.active_task.status == "waiting_confirmation"
    assert (tmp_path / "note.md").read_text(encoding="utf-8") == "# Note\n\nold line\n"
    assert "-old line" in first_turn.pending_confirmation.diff_preview
    assert "+new line" in first_turn.pending_confirmation.diff_preview


def test_confirm_pending_confirmation_executes_stored_tool_call_before_follow_up(tmp_path: Path) -> None:
    class WriteOnceThenAnswerLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201
            if not tool_names:
                return LLMResult(content="创建 notes.txt | code")
            tool_text = "\n".join(message.content for message in messages if message.role == "tool")
            if not tool_text:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-write-notes",
                            name="write_file",
                            args={"path": "notes.txt", "content": "confirmed write\n"},
                        )
                    ]
                )
            return LLMResult(content=f"已收到工具结果：{tool_text}")

    manager = SessionManager(tmp_path, runtime=AgentRuntime(llm=WriteOnceThenAnswerLLM()))

    first_turn = manager.handle_user_message("创建 notes.txt")

    assert first_turn.pending_confirmation is not None
    pending_tool_call_id = first_turn.pending_confirmation.tool_call_id
    assert not (tmp_path / "notes.txt").exists()

    second_turn = manager.handle_user_message("确认")

    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "confirmed write\n"
    assert second_turn.pending_confirmation is None
    assert any(
        message.role == "tool"
        and message.tool_call_id == pending_tool_call_id
        and "wrote notes.txt" in message.content
        for message in second_turn.messages
    )


def test_dry_run_does_not_write_files(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, runtime=AgentRuntime(dry_run=True, llm=ScriptedLLM()))

    session = manager.handle_user_message("在工作目录下新建 helloworld.py 文件")

    assert session.pending_confirmation is not None
    assert session.active_task is not None
    assert session.active_task.status == "waiting_confirmation"
    assert not (tmp_path / "helloworld.py").exists()
    assert session.active_task.observations[-1].summary.startswith("dry-run preview")
    assert any(event.data.get("dry_run") is True for event in session.active_task.trace_events)
    assert session.pending_confirmation is not None
    assert "--- a/helloworld.py" in session.pending_confirmation.diff_preview
    assert "+++ b/helloworld.py" in session.pending_confirmation.diff_preview


def test_react_loop_records_diff_preview_before_replace_in_file(tmp_path: Path) -> None:
    class ReplaceThenAnswerLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-test-before",
                            name="run_bash",
                            args={"command": "python -m pytest tests/test_app.py -q"},
                        )
                    ]
                )
            if self.calls == 2:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-replace",
                            name="replace_in_file",
                            args={
                                "path": "app.py",
                                "old_text": "return 'old'",
                                "new_text": "return 'new'",
                            },
                        )
                    ]
                )
            return LLMResult(content="ok")

    (tmp_path / "app.py").write_text("def value():\n    return 'old'\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text("def test_app():\n    assert True\n", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改代码 app.py", cwd=tmp_path)

    result = ReActLoop(ReplaceThenAnswerLLM(), ToolRegistry()).run(task, session)

    assert result == "即将修改: Replace text in app.py"
    assert session.pending_confirmation is not None
    assert session.pending_confirmation.tool_name == "replace_in_file"
    assert session.pending_confirmation.tool_call_id == "call-replace"
    assert "--- a/app.py" in session.pending_confirmation.diff_preview
    assert "-    return 'old'" in session.pending_confirmation.diff_preview
    assert "+    return 'new'" in session.pending_confirmation.diff_preview


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
    assert result.reason == "non-code task accepted without pytest reflection gate"


def test_reflection_loop_accepts_even_when_reflector_requests_replan(tmp_path: Path) -> None:
    class FakeReactLoop:
        def run(self, task: TaskState, session: SessionState) -> str:  # noqa: ARG002
            return "当前草稿"

    class ReplanningReflector:
        def decide(self, task: TaskState, draft: str):  # noqa: ANN001, ANN201, ARG002
            return type(
                "ReflectionDecision",
                (),
                {
                    "decision": "replan",
                    "reason": "需要重新规划",
                },
            )()

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    reflection = ReflectionLoop(react_loop=FakeReactLoop(), reflector=ReplanningReflector())

    result = reflection.run(task, session)

    assert result.accepted is True
    assert result.decision == "accept"
    assert result.content == "当前草稿"
    assert result.reason == "non-code task accepted without pytest reflection gate"


def test_runtime_respects_engineering_step_limit(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(default_limits=LoopLimits(max_engineering_steps=1), llm=ScriptedLLM())

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    assert result.active_task.step_count == 1


def test_runtime_stops_when_reflection_waits_for_user_choice(tmp_path: Path) -> None:
    class WaitingChoiceReflectionLoop:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, task: TaskState, session: SessionState) -> ReflectionResult:  # noqa: ARG002
            self.calls += 1
            return ReflectionResult(
                accepted=False,
                content="这里有三组可选文案，请选一个。",
                reason="等待用户选择后继续修改",
                decision="local_update",
            )

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(default_limits=LoopLimits(max_engineering_steps=3), llm=ScriptedLLM())
    reflection_loop = WaitingChoiceReflectionLoop()
    runtime.reflection_loop = reflection_loop

    result = runtime.on_user_message("给我几个文案选项", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert result.active_task.step_count == 1
    assert reflection_loop.calls == 1
    assert "三组可选文案" in result.messages[-1].content
    assert any("waiting for user choice" in event.message for event in result.active_task.trace_events)


def test_runtime_converts_agent_exception_to_failed_message(tmp_path: Path) -> None:
    class FailingReflectionLoop:
        def run(self, task: TaskState, session: SessionState):  # noqa: ARG002
            raise RuntimeError("LLM HTTP 400: bad request")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.reflection_loop = FailingReflectionLoop()

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    assert result.active_task.status == "failed"
    assert result.active_task.errors
    assert "LLM HTTP 400" in result.messages[-1].content


def test_runtime_forces_final_answer_after_react_iteration_limit(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(default_limits=LoopLimits(max_react_iterations=0), llm=ScriptedLLM())

    result = runtime.on_user_message("读取 a.md", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert not result.active_task.errors
    assert any("forcing final answer" in event.message for event in result.active_task.trace_events)


def test_runtime_marks_token_budget_exceeded_error_code(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("x" * 500))
    runtime = AgentRuntime(default_limits=LoopLimits(max_estimated_tokens=1), llm=ScriptedLLM())

    result = runtime.on_user_message("再补充一点", session)

    assert result.active_task is not None
    assert result.active_task.status == "failed"
    assert result.active_task.errors[0].code == "TOKEN_BUDGET_EXCEEDED"


def test_runtime_records_context_budget_usage_and_compression_trigger(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.model_context_limit = 100
    session.messages.extend(Message.user("x" * 120) for _ in range(4))
    runtime = AgentRuntime(
        default_limits=LoopLimits(max_estimated_tokens=100),
        logger=EventLogger(tmp_path / "logs", enabled=True),
        llm=ScriptedLLM(),
    )

    result = runtime.on_user_message("继续", session)

    assert result.active_task is not None
    log_path = session_event_log_path(tmp_path / "logs", result.session_id)
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    budget_rows = [row for row in rows if row.get("type") == "context_budget"]
    assert budget_rows
    row = budget_rows[0]
    assert row["estimated_tokens"] > 0
    assert row["model_context_limit"] == 100
    assert row["context_usage"] >= 0.70
    assert row["compression_triggered"] is True


def test_runtime_compresses_synchronously_after_user_message_and_logs_it(tmp_path: Path) -> None:
    class InspectingPlanner:
        def build_plan(self, goal, session, run_id=None):  # noqa: ANN001, ANN201, ARG002
            assert any(snapshot.metadata.get("trigger_stage") == "after_user_message" for snapshot in session.compression_snapshots)
            return [PlanStep(description="answer", intent="chat")], ""

    session = SessionState.create(cwd=tmp_path)
    session.model_context_limit = 1_000
    session.messages.extend(Message.user(f"历史 {index} " + ("x" * 180)) for index in range(8))
    runtime = AgentRuntime(
        default_limits=LoopLimits(max_estimated_tokens=1_000),
        logger=EventLogger(tmp_path / "logs", enabled=True),
        llm=ScriptedLLM(),
    )
    runtime.planner = InspectingPlanner()

    result = runtime.on_user_message("继续", session)

    assert result.compression_snapshots
    log_path = session_event_log_path(tmp_path / "logs", result.session_id)
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    completed = [row for row in rows if row.get("type") == "context_compression_completed"]
    assert completed
    assert completed[0]["trigger_stage"] == "after_user_message"
    assert completed[0]["strategies"]
    assert completed[0]["after_tokens"] < completed[0]["before_tokens"]


def test_runtime_persists_auto_compacted_context(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.model_context_limit = 1_000
    session.messages.extend(Message.user(f"历史 {index} " + ("x" * 180)) for index in range(12))
    before_tokens = estimate_message_tokens(session.messages)
    runtime = AgentRuntime(
        default_limits=LoopLimits(max_estimated_tokens=1_000),
        logger=EventLogger(tmp_path / "logs", enabled=True),
        llm=ScriptedLLM(),
    )

    result = runtime.on_user_message("继续", session)

    assert result.compression_snapshots
    assert estimate_message_tokens(result.messages) < before_tokens
    assert any(message.content.startswith("历史上下文摘要：") for message in result.messages)
    assert any(message.role == "system" and "已压缩较早的上下文" in message.content for message in result.messages)


def test_react_context_compression_uses_llm_summary_when_available(tmp_path: Path) -> None:
    class SummaryLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            return LLMResult(content="历史上下文摘要：\n- LLM 压缩摘要保留了关键决策。")

    llm = SummaryLLM()
    session = SessionState.create(cwd=tmp_path)
    session.messages.extend(Message.user(f"历史 {index} " + ("x" * 180)) for index in range(12))
    task = TaskState.create(goal="继续", cwd=tmp_path, limits=LoopLimits(max_estimated_tokens=1_000))
    task.model_context_limit = 1_000
    loop = ReActLoop(llm=llm)

    context = loop._conversation_context(task, session)

    assert llm.calls == 1
    assert context[0].content == "历史上下文摘要：\n- LLM 压缩摘要保留了关键决策。"
    assert context[0].content == "历史上下文摘要：\n- LLM 压缩摘要保留了关键决策。"


def test_react_compresses_synchronously_after_llm_message_before_tools(tmp_path: Path) -> None:
    class ToolInspectingExecutor(Executor):
        def execute(self, call, session_arg, task_arg):  # noqa: ANN001, ANN201, ARG002
            assert any(snapshot.metadata.get("trigger_stage") == "after_llm_message" for snapshot in session.compression_snapshots)
            return ToolResult(ok=True, summary="ok", content="done")

    class ToolLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    content="读取文件",
                    tool_calls=[ToolCall(id="call-1", name="read_file", args={"path": "README.md"})],
                )
            return LLMResult(content="完成")

    session = SessionState.create(cwd=tmp_path)
    session.model_context_limit = 1_000
    session.messages.extend(Message.user(f"历史 {index} " + ("x" * 130)) for index in range(8))
    task = TaskState.create(goal="继续", cwd=tmp_path, limits=LoopLimits(max_estimated_tokens=1_000))
    task.model_context_limit = 1_000
    loop = ReActLoop(llm=ToolLLM(), logger=EventLogger(tmp_path / "logs", enabled=True))
    loop.executor = ToolInspectingExecutor(loop.registry)

    loop.run(task, session)

    log_path = session_event_log_path(tmp_path / "logs", session.session_id)
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert any(
        row.get("type") == "context_compression_completed" and row.get("trigger_stage") == "after_llm_message"
        for row in rows
    )


def test_runtime_keeps_session_context_limit_stable(tmp_path: Path) -> None:
    class ChangingLimitLLM(ScriptedLLM):
        def context_limit(self) -> int:
            return 1_000_000

    session = SessionState.create(cwd=tmp_path)
    session.model_context_limit = 128_000
    runtime = AgentRuntime(
        logger=EventLogger(tmp_path / "logs", enabled=True),
        llm=ChangingLimitLLM(),
    )

    result = runtime.on_user_message("你好", session)

    assert result.model_context_limit == 128_000
    assert result.active_task is not None
    assert result.active_task.model_context_limit == 128_000


def test_runtime_exposes_active_task_before_reflection_runs(tmp_path: Path) -> None:
    class InspectingReflectionLoop:
        def run(self, task: TaskState, session: SessionState) -> ReflectionResult:
            assert session.active_task is task
            return ReflectionResult(accepted=True, content="ok", reason="accepted")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.reflection_loop = InspectingReflectionLoop()

    result = runtime.on_user_message("写报告", session)

    assert result.active_task is not None
    assert result.active_task.result == "ok"


def test_runtime_updates_plan_step_statuses_during_execution(tmp_path: Path) -> None:
    class InspectingReflectionLoop:
        def run(self, task: TaskState, session: SessionState) -> ReflectionResult:  # noqa: ARG002
            assert task.plan
            assert task.plan[0].status == "running"
            return ReflectionResult(accepted=True, content="ok", reason="accepted")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.reflection_loop = InspectingReflectionLoop()

    result = runtime.on_user_message("写报告", session)

    assert result.active_task is not None
    assert result.active_task.plan
    assert all(step.status == "done" for step in result.active_task.plan)


def test_runtime_replans_with_llm_planner_after_reflection_requests_replan(tmp_path: Path) -> None:
    class ReplanReflectionLoop:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, task: TaskState, session: SessionState) -> ReflectionResult:  # noqa: ARG002
            self.calls += 1
            return ReflectionResult(accepted=False, content="草稿", reason="needs more structure", decision="replan")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.reflection_loop = ReplanReflectionLoop()

    class PlanStepPlanner:
        def __init__(self) -> None:
            self.calls = []

        def build_plan(self, goal: str, session: SessionState):  # noqa: ANN001, ANN201
            self.calls.append(goal)
            from manus_mini.models import PlanStep

            return [
                PlanStep(description="重新识别目标并拆分执行步骤", intent="report"),
                PlanStep(description="补充当前草稿不足之处", intent="report"),
            ]

    planner = PlanStepPlanner()
    runtime.planner = planner

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    assert len(planner.calls) >= 2
    assert planner.calls[0] == "写一个报告"
    assert any("needs more structure" in goal for goal in planner.calls[1:])


def test_reflection_loop_carries_follow_up_context_into_next_round(tmp_path: Path) -> None:
    class FakeReactLoop:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, task: TaskState, session: SessionState) -> str:  # noqa: ARG002
            self.calls += 1
            task.observations.append(
                Observation(
                    tool_call_id="call-list-files",
                    ok=True,
                    summary="已列出 4 个文件",
                )
            )
            return "第一轮草稿"

    class FakeReflector:
        def decide(self, task: TaskState, draft: str):  # noqa: ANN001, ANN201, ARG002
            return type(
                "ReflectionDecision",
                (),
                {
                    "decision": "replan",
                    "reason": "需要基于已有结果继续",
                },
            )()

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="分析项目结构", cwd=tmp_path)
    reflection_loop = ReflectionLoop(react_loop=FakeReactLoop(), reflector=FakeReflector())

    result = reflection_loop.run(task, session)

    assert result.decision == "accept"
    assert result.reason == "non-code task accepted without pytest reflection gate"
    assert session.messages == []


def test_runtime_falls_back_on_invalid_llm_output(tmp_path: Path) -> None:
    class BrokenLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            raise ValueError("malformed output")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.react_loop.llm = BrokenLLM()

    result = runtime.on_user_message("写报告", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert result.messages[-1].content.startswith("已使用规则兜底生成草稿")
    assert "兜底原因：malformed output" in result.messages[-1].content
    assert any(
        event.phase == "llm" and "falling back to rule-based draft" in event.message
        for event in result.active_task.trace_events
    )


def test_runtime_falls_back_when_model_returns_raw_tool_call_markup(tmp_path: Path) -> None:
    class MarkupLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            raise ValueError("LLM emitted raw tool call markup in content")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.react_loop.llm = MarkupLLM()

    result = runtime.on_user_message("怎么启动和使用？", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert "manus-mini tui --cwd ." in result.messages[-1].content
    assert "<｜｜DSML｜｜tool_calls>" not in result.messages[-1].content


def test_runtime_fallback_for_identity_question_is_useful(tmp_path: Path) -> None:
    class BrokenLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            raise ValueError("network unavailable")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.react_loop.llm = BrokenLLM()

    result = runtime.on_user_message("你好，你是谁？", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert "我是 manus-mini" in result.messages[-1].content
    assert "兜底原因" not in result.messages[-1].content


def test_runtime_fallback_for_startup_question_is_useful(tmp_path: Path) -> None:
    class BrokenLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            raise ValueError("network unavailable")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.react_loop.llm = BrokenLLM()

    result = runtime.on_user_message("怎么启动和使用？", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert "pip install -e" in result.messages[-1].content
    assert "manus-mini tui --cwd ." in result.messages[-1].content


def test_runtime_replaces_empty_final_answer_with_fallback(tmp_path: Path) -> None:
    class EmptyLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(content="")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.react_loop.llm = EmptyLLM()

    result = runtime.on_user_message("如果模型不可用，你会怎么表现？", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert result.messages[-1].content.strip()
    assert "模型不可用" in result.messages[-1].content


def test_report_query_rejects_unsolicited_write_file_and_stays_in_chat(tmp_path: Path) -> None:
    class ReportToFileLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-write-report",
                            name="write_file",
                            args={"path": "docs/report.md", "content": "# report\n"},
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert "REPORT_WRITE_REQUIRES_EXPLICIT_REQUEST" in tool_messages[-1].content
            return LLMResult(content="下面直接在对话里给你摘要，不落文件。")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="请给我一份 AI Agent 框架行研摘要", cwd=tmp_path)

    result = ReActLoop(ReportToFileLLM()).run(task, session)

    assert result == "下面直接在对话里给你摘要，不落文件。"
    assert session.pending_confirmation is None


def test_report_query_rejects_unsolicited_shell_file_write_and_stays_in_chat(tmp_path: Path) -> None:
    class ReportShellWriteLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-shell-write-report",
                            name="run_bash",
                            args={
                                "command": "mkdir -p docs && cat <<'EOF' > docs/report.md\n# report\nEOF",
                                "confirmed": True,
                            },
                        )
                    ]
                )
            tool_messages = [message for message in messages if message.role == "tool"]
            assert "REPORT_WRITE_REQUIRES_EXPLICIT_REQUEST" in tool_messages[-1].content
            return LLMResult(content="下面直接在对话里给你摘要，不落文件。")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="请给我一份 AI Agent 框架行研摘要", cwd=tmp_path)

    result = ReActLoop(ReportShellWriteLLM()).run(task, session)

    assert result == "下面直接在对话里给你摘要，不落文件。"
    assert not (tmp_path / "docs" / "report.md").exists()
    assert session.pending_confirmation is None


def test_report_query_allows_explicit_write_to_file_phrase(tmp_path: Path) -> None:
    class ExplicitReportWriteLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(
                tool_calls=[
                    ToolCall(
                        id="call-write-report",
                        name="write_file",
                        args={"path": "docs/report.md", "content": "# report\n"},
                    )
                ]
            )

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="请给我一份 AI Agent 框架行研摘要，写到 docs/report.md", cwd=tmp_path)

    result = ReActLoop(ExplicitReportWriteLLM()).run(task, session)

    assert "REPORT_WRITE_REQUIRES_EXPLICIT_REQUEST" not in result
    assert session.pending_confirmation is not None
    assert session.pending_confirmation.tool_name == "write_file"


def test_react_loop_adds_disclaimer_when_web_search_has_no_results(tmp_path: Path) -> None:
    class NoResultsSearchTool:
        name = "web_search"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201, ARG002
            return ToolResult(
                tool_name=self.name,
                ok=True,
                summary="No results found for: AI Agent framework landscape",
                content="No results found.",
                data={"result_count": 0, "query": "AI Agent framework landscape"},
            )

    class SearchThenAnswerLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-search",
                            name="web_search",
                            args={"query": "AI Agent framework landscape"},
                        )
                    ]
                )
            return LLMResult(content="三类方向：编排型、多 Agent 协作型、运行时治理型。")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="请做一份 AI Agent 框架简短行研摘要", cwd=tmp_path)

    result = ReActLoop(SearchThenAnswerLLM(), ToolRegistry(tools=[NoResultsSearchTool()])).run(task, session)

    assert "未获取到有效搜索结果" in result
    assert "三类方向" in result


def test_react_loop_adds_disclaimer_when_web_search_fails(tmp_path: Path) -> None:
    class FailingSearchTool:
        name = "web_search"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201, ARG002
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="Search request failed: timeout",
                content="Search request failed: timeout",
                error_code="SEARCH_FAILED",
            )

    class SearchThenAnswerLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-search",
                            name="web_search",
                            args={"query": "AI Agent framework landscape"},
                        )
                    ]
                )
            return LLMResult(content="三类方向：编排型、多 Agent 协作型、运行时治理型。")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="请做一份 AI Agent 框架简短行研摘要", cwd=tmp_path)

    result = ReActLoop(SearchThenAnswerLLM(), ToolRegistry(tools=[FailingSearchTool()])).run(task, session)

    assert "未获取到有效搜索结果" in result
    assert "三类方向" in result


def test_react_loop_adds_disclaimer_when_webpage_fetches_fail_after_search(tmp_path: Path) -> None:
    class SearchWithResultTool:
        name = "web_search"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201, ARG002
            return ToolResult(
                tool_name=self.name,
                ok=True,
                summary="Found 1 results for: AI Agent framework landscape",
                content="1. Agent Framework Report\n   URL: https://example.com/report",
                data={"result_count": 1, "query": "AI Agent framework landscape"},
            )

    class FailingFetchTool:
        name = "fetch_webpage"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201, ARG002
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="Failed to fetch https://example.com/report: timeout",
                error_code="FETCH_FAILED",
            )

    class SearchFetchThenAnswerLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-search",
                            name="web_search",
                            args={"query": "AI Agent framework landscape"},
                        )
                    ]
                )
            if self.calls == 2:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-fetch",
                            name="fetch_webpage",
                            args={"url": "https://example.com/report"},
                        )
                    ]
                )
            return LLMResult(content="根据联网资料，AI Agent 框架可分为编排型和运行时治理型。")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="请做一份 AI Agent 框架简短行研摘要", cwd=tmp_path)

    result = ReActLoop(
        SearchFetchThenAnswerLLM(),
        ToolRegistry(tools=[SearchWithResultTool(), FailingFetchTool()]),
    ).run(task, session)

    assert "页面内容读取失败" in result
    assert "AI Agent 框架" in result


def test_runtime_accepts_complete_report_with_risk_discussion_without_looping(tmp_path: Path) -> None:
    class FakeReactLoop:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, task: TaskState, session: SessionState) -> str:  # noqa: ARG002
            self.calls += 1
            return (
                "以下是按 P0-P3 划分的优化建议：\n"
                "P0：补齐测试和异常处理。\n"
                "P1：收敛上下文压缩策略。\n"
                "P2：优化 TUI 呈现。\n"
                "P3：补充文档。\n"
                "风险：如果外部模型不可用，流程会退回规则草稿，但结果仍然可读。"
            )

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(default_limits=LoopLimits(max_engineering_steps=3), llm=ScriptedLLM())
    fake_react = FakeReactLoop()
    runtime.reflection_loop.react_loop = fake_react

    result = runtime.on_user_message("请你看下这个项目提一些优化建议，并给出P0-P3的优先级进行划分", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert fake_react.calls == 1
    assert "P0：补齐测试和异常处理" in result.messages[-1].content


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
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.react_loop.llm = BrokenLLM()

    result = runtime.on_user_message("请检查项目", session)

    assert result.active_task is not None
    assert "最近工具结果" in result.messages[-1].content
    assert "read README.md" in result.messages[-1].content


def test_runtime_logs_llm_request_and_response_payloads(tmp_path: Path) -> None:
    runtime = AgentRuntime(logger=EventLogger(tmp_path / "logs", enabled=True), llm=ScriptedLLM())
    session = SessionState.create(cwd=tmp_path)

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    log_path = session_event_log_path(tmp_path / "logs", result.session_id)
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    request_rows = [row for row in rows if row.get("type") == "llm_request"]
    response_rows = [row for row in rows if row.get("type") == "llm_response"]
    assert request_rows
    assert response_rows
    planner_request = next(row for row in request_rows if row.get("stage") == "planner")
    planner_response = next(row for row in response_rows if row.get("stage") == "planner")
    react_request = next(row for row in request_rows if row.get("stage") == "react")
    react_response = next(row for row in response_rows if row.get("stage") == "react")
    assert planner_request["request"]["messages"]
    assert planner_request["request"]["tool_names"] == []
    assert "api_request_payload" not in planner_request
    assert "request" not in planner_response
    assert "api_request_payload" not in planner_response
    assert "api_response_raw" not in planner_response
    assert "response" in planner_response
    assert react_request["request"]["messages"]
    assert react_request["request"]["tool_names"]
    assert "api_request_payload" not in react_request
    assert "request" not in react_response
    assert "api_request_payload" not in react_response
    assert "api_response_raw" not in react_response
    assert "response" in react_response
    reflection_rows = [row for row in rows if row.get("type") == "reflection"]
    assert reflection_rows
    assert "decision" in reflection_rows[0]
    assert "reason" in reflection_rows[0]
    assert "draft_preview" in reflection_rows[0]
    assert "draft" in reflection_rows[0]
    assert "reflection_context" in reflection_rows[0]
    assert "user_goal" in reflection_rows[0]["reflection_context"]
    assert "plan" in reflection_rows[0]["reflection_context"]
    assert "observations" in reflection_rows[0]["reflection_context"]


def test_format_tool_result_message_truncates_large_content_and_paths() -> None:
    from manus_mini.tools.base import ToolResult

    result = ToolResult(
        tool_name="read_file",
        ok=True,
        summary="read big.md",
        paths=[f"file-{index}.md" for index in range(50)],
        content="x" * 5000,
    )

    message = format_tool_result_message(result)

    assert "read big.md" in message
    assert "file-0.md" in message
    assert "file-49.md" not in message
    assert "x" * 5000 not in message
    assert "truncated" in message


def test_react_loop_finishes_when_large_tool_results_are_summarized(tmp_path: Path) -> None:
    from manus_mini.tools.base import ToolResult

    class HugeListTool:
        name = "list_files"
        risk_level = "safe"
        requires_confirmation = False
        is_read_only = True

        def preview(self, **kwargs):  # noqa: ANN001, ANN201
            raise NotImplementedError

        def resource_keys(self, **kwargs):  # noqa: ANN001, ANN201
            return []

        def run(self, **kwargs):  # noqa: ANN001, ANN201
            return ToolResult(
                tool_name=self.name,
                ok=True,
                summary="found many files",
                paths=[f"file-{index}.md" for index in range(100)],
            )

    class SizeSensitiveLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-list", name="list_files", args={"path": "."})])
            tool_messages = [message for message in messages if message.role == "tool"]
            assert tool_messages
            assert "truncated" in tool_messages[-1].content
            return LLMResult(content="已获得足够信息，开始总结。")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="分析项目并给建议", cwd=tmp_path)
    result = ReActLoop(SizeSensitiveLLM(), ToolRegistry(tools=[HugeListTool()])).run(task, session)

    assert result == "已获得足够信息，开始总结。"


def test_runtime_logs_full_llm_request_payload_including_tool_messages(tmp_path: Path) -> None:
    class ToolThenAnswerLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    content="先读取 README。",
                    tool_calls=[ToolCall(id="call-read-readme", name="read_file", args={"path": "README.md"})],
                )
            return LLMResult(content="完成")

    (tmp_path / "README.md").write_text("hello world", encoding="utf-8")
    runtime = AgentRuntime(logger=EventLogger(tmp_path / "logs", enabled=True), llm=ScriptedLLM())
    runtime.react_loop.llm = ToolThenAnswerLLM()
    session = SessionState.create(cwd=tmp_path)

    result = runtime.on_user_message("读取 README.md", session)

    assert result.active_task is not None
    log_path = session_event_log_path(tmp_path / "logs", result.session_id)
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    request_rows = [row for row in rows if row.get("type") == "llm_request" and row.get("stage") == "react"]
    assert request_rows
    payload_messages = request_rows[-1]["request"]["messages"]
    assert any(message["role"] == "tool" for message in payload_messages)
    assert any(message["role"] == "assistant" and message.get("tool_calls") for message in payload_messages)
    assert any("hello world" in message.get("content", "") for message in payload_messages if message["role"] == "tool")


def test_runtime_handles_keyboard_interrupt_and_logs_interrupt(tmp_path: Path) -> None:
    class InterruptingLLM:
        def context_limit(self) -> int | None:
            return 1_000

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            raise KeyboardInterrupt

    runtime = AgentRuntime(logger=EventLogger(tmp_path / "logs", enabled=True), llm=ScriptedLLM())
    runtime.react_loop.llm = InterruptingLLM()
    session = SessionState.create(cwd=tmp_path)

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    assert result.active_task.status == "failed"
    assert "用户中断" in result.active_task.result
    log_path = session_event_log_path(tmp_path / "logs", result.session_id)
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    interrupt_rows = [row for row in rows if row.get("type") == "interrupt"]
    assert interrupt_rows
    assert interrupt_rows[0]["code"] == "USER_CANCELLED"


def test_runtime_logs_successful_read_file_content_in_logs(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("secret file content", encoding="utf-8")

    class ReadReadmeLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(
                    content="我先读取 README。",
                    tool_calls=[ToolCall(id="call-read-readme", name="read_file", args={"path": "README.md"})],
                )
            return LLMResult(content="读取完成")

    runtime = AgentRuntime(logger=EventLogger(tmp_path / "logs", enabled=True), llm=ScriptedLLM())
    runtime.react_loop.llm = ReadReadmeLLM()
    session = SessionState.create(cwd=tmp_path)

    result = runtime.on_user_message("读取 README.md", session)

    assert result.active_task is not None
    read_events = [
        event
        for event in result.active_task.trace_events
        if event.phase == "tool" and event.data.get("tool_name") == "read_file"
    ]
    assert read_events
    assert "content_preview" not in read_events[-1].data

    log_path = session_event_log_path(tmp_path / "logs", result.session_id)
    log_text = log_path.read_text(encoding="utf-8")
    assert "secret file content" in log_text
    assert "read_file" in log_text
    assert "secret file content" in log_text


def test_runtime_has_no_total_runtime_limit(tmp_path: Path) -> None:
    class SlowReflectionLoop:
        def run(self, task: TaskState, session: SessionState) -> ReflectionResult:  # noqa: ARG002
            sleep(0.03)
            return ReflectionResult(accepted=True, content="completed after waiting", reason="accepted")

    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.reflection_loop = SlowReflectionLoop()

    result = runtime.on_user_message("写报告", session)

    assert result.active_task is not None
    assert result.active_task.status == "done"
    assert result.active_task.errors == []
    assert "completed after waiting" in result.messages[-1].content


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
    runtime = AgentRuntime(reporter=Reporter(tmp_path / "outputs"), llm=ScriptedLLM())
    runtime.reflection_loop = SecretReflectionLoop()

    result = runtime.on_user_message("我的 api_key 是 sk-live-secret", session)

    assert result.active_task is not None
    report = result.active_task.artifacts[-1].path.read_text(encoding="utf-8")
    assert "sk-live-secret" not in report
    assert "password=abc123" not in report
    assert "[REDACTED]" in report
