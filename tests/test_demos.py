from pathlib import Path

from vora.llm import LLMResult
from vora.memory import MemoryManager
from vora.models import Message, SessionState, TaskState, ToolCall
from vora.react import ReActLoop
from vora.runtime import AgentRuntime
from vora.session import SessionManager
from vora.tools import ToolRegistry
from support import ScriptedLLM


def test_demo_research_flow_reads_docs_and_writes_report(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# demo\n\nThis project is a TUI agent.", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "design.md").write_text("design notes", encoding="utf-8")

    class ResearchLLM:
        def __init__(self) -> None:
            self.calls = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            if self.calls == 1:
                return LLMResult(tool_calls=[ToolCall(id="call-list", name="list_files", args={"path": "."})])
            if self.calls == 2:
                return LLMResult(
                    tool_calls=[
                        ToolCall(id="call-read-readme", name="read_file", args={"path": "README.md"}),
                        ToolCall(id="call-read-design", name="read_file", args={"path": "docs/design.md"}),
                    ]
                )
            return LLMResult(content="research report ready")

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="阅读 docs 目录，生成项目背景报告", cwd=tmp_path)

    result = ReActLoop(ResearchLLM(), ToolRegistry()).run(task, session)

    assert result == "research report ready"
    assert any(event.data.get("tool_name") == "read_file" for event in task.trace_events if event.phase == "tool")


def test_demo_code_flow_confirms_write_and_modifies_previous_artifact(tmp_path: Path) -> None:
    class WriteThenEditLLM:
        def __init__(self) -> None:
            self.initial_writes = 0
            self.edits = 0

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            system_text = " ".join(
                getattr(message, "content", "")
                for message in messages
                if getattr(message, "role", "") == "system"
            )
            if "反思审查器" in system_text:
                return LLMResult(content='{"decision":"accept","reason":"demo draft accepted"}')
            if not tool_names:
                return LLMResult(content="1. 执行文件写入 | code")
            user_text = " ".join(
                getattr(message, "content", "")
                for message in messages
                if getattr(message, "role", "") == "user"
            )
            if "第二版" not in user_text and self.initial_writes < 2:
                self.initial_writes += 1
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-write",
                            name="write_file",
                            args={"path": "notes.txt", "content": "version-1"},
                        )
                    ]
                )
            if "第二版" not in user_text:
                return LLMResult(content="version-1 saved")
            if self.edits < 2:
                self.edits += 1
                return LLMResult(
                    tool_calls=[
                        ToolCall(
                            id="call-edit",
                            name="write_file",
                            args={"path": "notes.txt", "content": "version-2"},
                        )
                    ]
                )
            return LLMResult(content="version-2 saved")

    manager = SessionManager(tmp_path, runtime=AgentRuntime(llm=ScriptedLLM()))
    manager.runtime.react_loop.llm = WriteThenEditLLM()
    manager.runtime.reflection_loop.react_loop.llm = manager.runtime.react_loop.llm

    first_turn = manager.handle_user_message("新建 notes.txt")
    assert first_turn.pending_confirmation is None
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "version-1"

    second_turn = manager.handle_user_message("把上一轮产物改成第二版")
    assert second_turn.pending_confirmation is None
    assert second_turn.active_task is not None
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "version-2"


def test_demo_automation_flow_formats_todo_list() -> None:
    from vora.tools.automation_tools import extract_todos, generate_checklist, organize_notes

    inbox = "\n".join(["TODO: review docs", "- ship release", "meeting note", "* polish output"])
    todos = extract_todos(inbox)
    checklist = generate_checklist(todos)
    notes = organize_notes(["split tasks", "assign owner"])

    assert todos == ["TODO: review docs", "- ship release", "* polish output"]
    assert "- [ ] TODO: review docs" in checklist
    assert "- split tasks" in notes


def test_demo_memory_preference_is_injected_on_follow_up(tmp_path: Path) -> None:
    memory_manager = MemoryManager(tmp_path / "memory.db")
    memory_manager.add(scope="user", kind="preference", content="用户偏好：尽量用 Markdown。", tags=["preference", "markdown"])
    runtime = AgentRuntime(memory_manager=memory_manager, llm=ScriptedLLM())
    session = SessionState.create(cwd=tmp_path)

    result = runtime.on_user_message("请根据我的偏好输出一段建议", session)

    assert result.messages[-1].role == "agent"
    assert any(message.role == "system" and "长期记忆" in message.content for message in result.messages)


def test_demo_long_session_compresses_context(tmp_path: Path) -> None:
    runtime = AgentRuntime(llm=ScriptedLLM())
    runtime.default_limits.max_estimated_tokens = 70
    session = SessionState.create(cwd=tmp_path)
    session.messages.extend(Message.user(f"message {index}") for index in range(20))

    result = runtime.on_user_message("继续", session)

    assert result.compression_snapshots
    assert any(message.role == "system" and "已压缩较早的上下文" in message.content for message in result.messages)
    compression_notice = next(message.content for message in result.messages if message.role == "system" and "已压缩较早的上下文" in message.content)
    assert "压缩前估算" in compression_notice
    assert "目标预算" in compression_notice
    assert "保留消息" in compression_notice
