from pathlib import Path

from manus_mini.session import SessionManager


def test_session_manager_creates_empty_session(tmp_path: Path) -> None:
    manager = SessionManager(cwd=tmp_path)

    assert manager.current.cwd == tmp_path
    assert manager.current.messages == []
    assert manager.current.active_task is None


def test_session_manager_handles_user_message(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello world", encoding="utf-8")
    manager = SessionManager(cwd=tmp_path)

    session = manager.handle_user_message("读取 a.md")

    assert session.messages[0].content == "读取 a.md"
    assert session.messages[-1].role == "agent"
    assert session.active_task is not None
    assert "hello world" in session.messages[-1].content
