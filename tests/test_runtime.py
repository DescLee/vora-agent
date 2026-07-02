import json
from pathlib import Path
import re
import tempfile
from time import perf_counter, sleep

from manus_mini.llm import LLMResult
from manus_mini.models import LoopLimits, Message, Observation, SessionState, TaskState, ToolCall, TraceEvent
from manus_mini.react import ReActLoop, format_tool_result_message
from manus_mini.reflection import ReflectionLoop, ReflectionResult
from manus_mini.logging import EventLogger
from manus_mini.reporter import Reporter
from manus_mini.runtime import AgentRuntime
from manus_mini.session import SessionManager
from manus_mini.tools.base import ToolResult
from manus_mini.tools.registry import ToolRegistry
from support import ScriptedLLM


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
    assert any(message.tool_call_id == "call-read-readme" for message in result.messages if message.role == "tool")


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
    runtime = AgentRuntime(reporter=Reporter(tmp_path / "outputs"), llm=ScriptedLLM())

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
    summary_dir = tmp_path / "runs" / f"{result.session_id}-{result.active_task.run_id}"
    summary_files = list(summary_dir.glob("summary-*.md"))
    assert len(summary_files) == 1
    assert re.match(r"^summary-\d{8}-\d{6}\.md$", summary_files[0].name)
    assert "Manus Mini Run Summary" in summary_files[0].read_text(encoding="utf-8")


def test_runtime_output_filename_starts_with_timestamp_for_lookup(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(reporter=Reporter(tmp_path / "outputs"), llm=ScriptedLLM())

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    filename = result.active_task.artifacts[-1].path.name
    assert re.match(r"^\d{8}-\d{6}-run-[a-f0-9]{12}\.md$", filename)


def test_runtime_defaults_to_temp_reporter_output_dir_under_pytest(tmp_path: Path) -> None:
    runtime = AgentRuntime(llm=ScriptedLLM())

    assert runtime.reporter.output_dir == Path(tempfile.gettempdir()) / "manus-mini" / "outputs"
    assert ".manus-mini/projects/" in str(runtime.logger.root)
    assert str(runtime.logger.root).endswith("/runs")
    assert runtime.reporter.run_root is not None
    assert runtime.reporter.run_root == Path(tempfile.gettempdir()) / "manus-mini" / "runs"

    session = SessionState.create(cwd=tmp_path)
    runtime.on_user_message("你好", session)

    assert not (tmp_path / "runs").exists()
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


def test_react_loop_limits_overview_task_to_project_entry_files(tmp_path: Path) -> None:
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
            assert "PROJECT_SCOPE_RESTRICTED" in tool_messages["call-read-deep"]
            return LLMResult(content="overview handled")

    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    (tmp_path / "src" / "feature").mkdir(parents=True)
    (tmp_path / "src" / "feature" / "deep.py").write_text("SECRET = 'should not be read'", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="请你看下这个项目提一些优化建议", cwd=tmp_path)

    result = ReActLoop(OverviewLLM(), ToolRegistry()).run(task, session)

    assert result == "overview handled"
    blocked = [observation for observation in task.observations if observation.tool_call_id == "call-read-deep"]
    assert blocked
    assert blocked[0].ok is False
    assert "SECRET" not in blocked[0].content


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
                return LLMResult(tool_calls=[ToolCall(id="call-bash-1", name="run_bash", args={"command": "printf x >> marker.txt"})])
            if self.calls == 2:
                return LLMResult(tool_calls=[ToolCall(id="call-bash-2", name="run_bash", args={"command": "printf x >> marker.txt"})])
            return LLMResult(content="ok")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="重复执行命令", cwd=tmp_path)

    result = ReActLoop(RepeatingCommandLLM(), ToolRegistry()).run(task, session)

    assert result == "ok"
    assert (tmp_path / "marker.txt").read_text(encoding="utf-8") == "x"
    command_events = [
        event for event in task.trace_events
        if event.phase == "tool" and event.data.get("tool_name") == "run_bash"
    ]
    assert [event.data.get("error_code") for event in command_events] == [None, "DUPLICATE_TOOL_CALL_BLOCKED"]
    assert command_events[1].data.get("blocked_duplicate") is True


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
                            id="call-replace",
                            name="replace_in_file",
                            args={"path": "app.py", "old_text": "old", "new_text": "new"},
                        )
                    ]
                )
            if self.calls == 2:
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-test",
                            name="run_bash",
                            args={"command": "python -m pytest tests/test_app.py -q", "timeout_seconds": 30},
                        )
                    ]
                )
            if self.calls == 3:
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
    assert llm.calls == 3
    list_events = [event for event in task.trace_events if event.phase == "tool" and event.data.get("tool_name") == "list_files"]
    assert list_events == []
    assert any(event.message == "Code change validated; forcing final answer" for event in task.trace_events)


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
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改代码 app.py", cwd=tmp_path)

    result = ReActLoop(ReplaceThenAnswerLLM(), ToolRegistry()).run(task, session)

    assert result == "ok"
    diff_events = [
        event for event in task.trace_events
        if event.phase == "tool" and event.data.get("message_type") == "diff_preview"
    ]
    assert len(diff_events) == 1
    assert diff_events[0].data["tool_name"] == "replace_in_file"
    assert diff_events[0].data["tool_call_id"] == "call-replace"
    assert "--- a/app.py" in diff_events[0].data["diff_preview"]
    assert "-    return 'old'" in diff_events[0].data["diff_preview"]
    assert "+    return 'new'" in diff_events[0].data["diff_preview"]
    replace_events = [
        event for event in task.trace_events
        if event.phase == "tool" and event.data.get("tool_call_id") == "call-replace" and "ok" in event.data
    ]
    assert task.trace_events.index(diff_events[0]) < task.trace_events.index(replace_events[0])


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
    session.messages.extend(Message.user("x" * 120) for _ in range(4))
    runtime = AgentRuntime(
        default_limits=LoopLimits(max_estimated_tokens=100),
        logger=EventLogger(tmp_path / "runs", enabled=True),
        llm=ScriptedLLM(),
    )

    result = runtime.on_user_message("继续", session)

    assert result.active_task is not None
    log_path = next((tmp_path / "runs" / f"{result.session_id}-{result.active_task.run_id}").glob("*-event.jsonl"))
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

    assert result.decision == "replan"
    assert session.messages
    assert session.messages[-1].role == "system"
    assert "上一轮已完成的进展" in session.messages[-1].content
    assert "第一轮草稿" in session.messages[-1].content
    assert "已列出 4 个文件" in session.messages[-1].content


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
    runtime = AgentRuntime(logger=EventLogger(tmp_path / "runs", enabled=True), llm=ScriptedLLM())
    session = SessionState.create(cwd=tmp_path)

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    log_path = next((tmp_path / "runs" / f"{result.session_id}-{result.active_task.run_id}").glob("*-event.jsonl"))
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
    runtime = AgentRuntime(logger=EventLogger(tmp_path / "runs", enabled=True), llm=ScriptedLLM())
    runtime.react_loop.llm = ToolThenAnswerLLM()
    session = SessionState.create(cwd=tmp_path)

    result = runtime.on_user_message("读取 README.md", session)

    assert result.active_task is not None
    log_path = next((tmp_path / "runs" / f"{result.session_id}-{result.active_task.run_id}").glob("*-event.jsonl"))
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

    runtime = AgentRuntime(logger=EventLogger(tmp_path / "runs", enabled=True), llm=ScriptedLLM())
    runtime.react_loop.llm = InterruptingLLM()
    session = SessionState.create(cwd=tmp_path)

    result = runtime.on_user_message("写一个报告", session)

    assert result.active_task is not None
    assert result.active_task.status == "failed"
    assert "用户中断" in result.active_task.result
    log_path = next((tmp_path / "runs" / f"{result.session_id}-{result.active_task.run_id}").glob("*-event.jsonl"))
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

    runtime = AgentRuntime(logger=EventLogger(tmp_path / "runs", enabled=True), llm=ScriptedLLM())
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

    log_path = next((tmp_path / "runs" / f"{result.session_id}-{result.active_task.run_id}").glob("*-event.jsonl"))
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
