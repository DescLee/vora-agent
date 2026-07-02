from pathlib import Path

from manus_mini.models import Message
from manus_mini.runtime import AgentRuntime
from manus_mini.session import SessionManager
from manus_mini.session_store import SessionStore
from support import ScriptedLLM


def test_session_manager_creates_empty_session(tmp_path: Path) -> None:
    manager = SessionManager(cwd=tmp_path)

    assert manager.current.cwd == tmp_path
    assert manager.current.messages == []
    assert manager.current.active_task is None


def test_session_manager_handles_user_message(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello world", encoding="utf-8")
    manager = SessionManager(cwd=tmp_path, runtime=AgentRuntime(llm=ScriptedLLM()))

    session = manager.handle_user_message("读取 a.md")

    assert session.messages[0].content == "读取 a.md"
    assert session.messages[-1].role == "agent"
    assert session.active_task is not None
    assert "hello world" in session.messages[-1].content


def test_session_manager_saves_session_after_turn(tmp_path: Path) -> None:
    manager = SessionManager(cwd=tmp_path, runtime=AgentRuntime(llm=ScriptedLLM()))

    session = manager.handle_user_message("你好")
    loaded = SessionStore(tmp_path).load(session.session_id)

    assert loaded.session_id == session.session_id
    assert loaded.messages[0].content == "你好"
    assert loaded.messages[-1].role == "agent"


def test_session_manager_save_context_command_writes_timestamped_snapshot(tmp_path: Path) -> None:
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


def test_session_manager_help_command_lists_available_commands(tmp_path: Path) -> None:
    manager = SessionManager(cwd=tmp_path)

    session = manager.handle_user_message("/help")

    help_text = session.messages[-1].content
    assert "可用指令" in help_text
    assert "/save-context" in help_text
    assert "/compact" in help_text
    assert "manus-mini list" in help_text
    assert "manus-mini resume" in help_text


def test_session_manager_saves_state_when_runtime_is_interrupted(tmp_path: Path) -> None:
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
