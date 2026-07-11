from pathlib import Path

from vora.context import validate_tool_call_pairs
from vora.models import Message, PendingConfirmation, SessionState, TaskState
from vora.runtime import AgentRuntime
from vora.session import SessionManager
from vora.session_store import SessionStore
from support import ScriptedLLM


def test_session_manager_creates_empty_session(tmp_path: Path) -> None:
    manager = SessionManager(cwd=tmp_path)

    assert manager.current.cwd == tmp_path
    assert manager.current.messages == []
    assert manager.current.active_task is None
    assert manager.current.model_context_limit == 128_000


def test_session_manager_resolves_model_context_limit_once(tmp_path: Path) -> None:
    class ChangingLimitLLM(ScriptedLLM):
        def __init__(self) -> None:
            self.calls = 0

        def context_limit(self) -> int:
            self.calls += 1
            return 1_000_000 if self.calls == 1 else 128_000

    llm = ChangingLimitLLM()
    manager = SessionManager(cwd=tmp_path, runtime=AgentRuntime(llm=llm))

    assert manager.current.model_context_limit == 1_000_000
    assert llm.calls == 1

    manager.current.model_context_limit = 777_000
    manager._ensure_session_model_context_limit()

    assert manager.current.model_context_limit == 777_000
    assert llm.calls == 1


def test_session_manager_handles_user_message(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "a.md").write_text("hello world", encoding="utf-8")
    manager = SessionManager(cwd=tmp_path, runtime=AgentRuntime(llm=ScriptedLLM()))

    session = manager.handle_user_message("读取 a.md")

    assert session.messages[0].content == "读取 a.md"
    assert session.messages[-1].role == "agent"
    assert session.active_task is not None
    assert "hello world" in session.messages[-1].content


def test_session_manager_saves_session_after_turn(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    manager = SessionManager(cwd=tmp_path, runtime=AgentRuntime(llm=ScriptedLLM()))

    session = manager.handle_user_message("你好")
    loaded = SessionStore(tmp_path).load(session.session_id)

    assert loaded.session_id == session.session_id
    assert loaded.messages[0].content == "你好"
    assert loaded.messages[-1].role == "agent"


def test_session_manager_save_context_command_writes_timestamped_snapshot(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    manager = SessionManager(cwd=tmp_path)
    manager.current.messages.append(Message.user("学习用上下文"))
    manager.current.messages.append(Message.agent("这是当前回答"))

    session = manager.handle_user_message("/save-context")

    snapshots = list(tmp_path.glob("context-*"))
    assert len(snapshots) == 1
    assert snapshots[0].is_dir()
    assert (snapshots[0] / "session.json").exists()
    context_md = (snapshots[0] / "context.md").read_text(encoding="utf-8")
    assert "学习用上下文" in context_md
    assert "这是当前回答" in context_md
    assert "已保存当前上下文" in session.messages[-1].content


def test_session_manager_save_context_snapshot_redacts_sensitive_content(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    manager = SessionManager(cwd=tmp_path)
    manager.current.messages.append(Message.user("我的 token=secret-token"))
    manager.current.messages.append(Message.agent("结果包含 password=abc123"))

    manager.handle_user_message("/save-context")

    snapshot = next(tmp_path.glob("context-*"))
    session_json = (snapshot / "session.json").read_text(encoding="utf-8")
    context_md = (snapshot / "context.md").read_text(encoding="utf-8")
    exported = session_json + context_md
    assert "secret-token" not in exported
    assert "abc123" not in exported
    assert "token=[REDACTED]" in exported
    assert "password=[REDACTED]" in exported


def test_session_manager_help_command_lists_available_commands(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    manager = SessionManager(cwd=tmp_path)

    session = manager.handle_user_message("/help")

    help_text = session.messages[-1].content
    assert "可用指令" in help_text
    assert "/save-context" in help_text
    assert "/compact" in help_text
    assert "vora list" in help_text
    assert "vora resume" in help_text


def test_session_manager_keeps_pending_confirmation_when_user_sends_unrelated_message(tmp_path: Path) -> None:
    class RuntimeShouldNotRun:
        def on_user_message(self, content: str, session, append_user_message: bool = True):  # noqa: ANN001, ARG002
            raise AssertionError("runtime should not run while a confirmation is pending")

    session = SessionState.create(cwd=tmp_path)
    session.active_task = TaskState.create(goal="创建文件", cwd=tmp_path)
    session.pending_confirmation = PendingConfirmation(
        tool_call_id="call-write",
        tool_name="write_file",
        tool_args={"path": "note.md", "content": "hello"},
        summary="Write note.md",
    )
    manager = SessionManager(cwd=tmp_path, runtime=RuntimeShouldNotRun(), initial_session=session)

    result = manager.handle_user_message("先不用改，告诉我这个项目是什么")

    assert result.pending_confirmation is not None
    assert result.pending_confirmation.tool_call_id == "call-write"
    assert result.messages[-1].role == "system"
    assert "请先输入 `确认` 或 `取消`" in result.messages[-1].content


def test_session_manager_saves_state_when_runtime_is_interrupted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    class InterruptingRuntime:
        def on_user_message(self, content: str, session, append_user_message: bool = True):  # noqa: ANN001, ARG002
            raise KeyboardInterrupt

    manager = SessionManager(cwd=tmp_path, runtime=InterruptingRuntime())

    session = manager.handle_user_message("写一个报告")
    loaded = SessionStore(tmp_path).load(session.session_id)

    assert session is manager.current
    assert loaded.messages[-1].role == "system"
    assert "用户中断" in loaded.messages[-1].content
    assert loaded.active_task is None or loaded.active_task.status == "failed"


def test_session_manager_interruption_completes_pending_tool_messages(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.active_task = TaskState.create(goal="读取文件", cwd=tmp_path)
    session.messages.append(Message.user("读取文件"))
    session.messages.append(Message.agent("需要读取", tool_call_ids=["call-read"]))
    manager = SessionManager(cwd=tmp_path, initial_session=session)

    manager._mark_current_interrupted()

    assert manager.current.active_task is not None
    assert manager.current.active_task.status == "failed"
    validate_tool_call_pairs(manager.current.messages[:-1])
    tool_messages = [message for message in manager.current.messages if message.role == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].tool_call_id == "call-read"
    assert "USER_CANCELLED" in tool_messages[0].content


def test_session_store_repairs_interrupted_tool_messages_on_load(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("读取文件"))
    session.messages.append(Message.agent("需要读取", tool_call_ids=["call-read"]))
    store.save(session)

    loaded = store.load(session.session_id)

    validate_tool_call_pairs(loaded.messages)
    assert loaded.messages[-1].role == "tool"
    assert loaded.messages[-1].tool_call_id == "call-read"
    assert "USER_CANCELLED" in loaded.messages[-1].content
