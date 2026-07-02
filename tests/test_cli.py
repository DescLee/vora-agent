from pathlib import Path

from manus_mini.cli import main
from manus_mini.models import Message, SessionState
from manus_mini.session_store import SessionStore


def test_cli_list_prints_saved_sessions_without_opening_tui(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("上一轮问题"))
    session.messages.append(Message.agent("上一轮回答"))
    store.save(session)

    main(["list", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert session.session_id in out
    assert "上一轮问题" in out


def test_cli_resume_loads_session_and_skips_tui_open(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    store.save(session)
    seen = {}

    def fake_run(self):  # noqa: ANN001
        seen["initial_session"] = self.manager.current.session_id

    monkeypatch.setattr("manus_mini.prompt_tui.PromptTui.run", fake_run)

    main(["resume", session.session_id, "--cwd", str(tmp_path)])

    assert seen["initial_session"] == session.session_id
