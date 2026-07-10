from pathlib import Path

import pytest

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


def test_cli_list_prints_session_directory_when_empty(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    main(["list", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert "No saved sessions." in out
    assert str(SessionStore(tmp_path).sessions_dir) in out


def test_cli_list_redacts_and_truncates_last_user_message(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    long_tail = "x" * 200
    session.messages.append(Message.user(f"请处理 token=secret-token {long_tail}"))
    store.save(session)

    main(["list", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert "secret-token" not in out
    assert "token=[REDACTED]" in out
    assert long_tail not in out
    assert "..." in out


def test_cli_list_skips_corrupt_session_files(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("保留的会话"))
    store.save(session)
    store.sessions_dir.mkdir(parents=True, exist_ok=True)
    (store.sessions_dir / "broken.json").write_text("{not valid json", encoding="utf-8")

    main(["list", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert session.session_id in out
    assert "保留的会话" in out
    assert "broken.json" not in out


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


def test_cli_resume_missing_session_prints_friendly_error(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    with pytest.raises(SystemExit) as error:
        main(["resume", "missing-session", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert error.value.code == 1
    assert "Error: session 'missing-session' not found." in out


def test_cli_resume_corrupt_session_prints_friendly_error(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    store.sessions_dir.mkdir(parents=True, exist_ok=True)
    (store.sessions_dir / "broken-session.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main(["resume", "broken-session", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert error.value.code == 1
    assert "Error: session 'broken-session' is unreadable or corrupt." in out


def test_cli_resume_invalid_session_id_prints_friendly_error(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    with pytest.raises(SystemExit) as error:
        main(["resume", "../outside", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert error.value.code == 1
    assert "Error: invalid session id '../outside'." in out


def test_cli_remove_invalid_session_id_prints_friendly_error(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    with pytest.raises(SystemExit) as error:
        main(["remove", "../sessions", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert error.value.code == 1
    assert "Error: invalid session id '../sessions'." in out


def test_cli_tui_defaults_to_ninety_nine_react_iterations(tmp_path: Path, monkeypatch) -> None:
    seen = {}

    def fake_run(self):  # noqa: ANN001
        seen["max_react_iterations"] = self.manager.runtime.default_limits.max_react_iterations

    monkeypatch.setattr("manus_mini.prompt_tui.PromptTui.run", fake_run)

    main(["tui", "--cwd", str(tmp_path)])

    assert seen["max_react_iterations"] == 99


def test_cli_accepts_global_cwd_without_explicit_tui_subcommand(tmp_path: Path, monkeypatch) -> None:
    seen = {}

    def fake_run(self):  # noqa: ANN001
        seen["cwd"] = self.manager.current.cwd
        seen["max_react_iterations"] = self.manager.runtime.default_limits.max_react_iterations

    monkeypatch.setattr("manus_mini.prompt_tui.PromptTui.run", fake_run)

    main(["--cwd", str(tmp_path)])

    assert seen["cwd"] == tmp_path
    assert seen["max_react_iterations"] == 99


def test_cli_rejects_unknown_subcommand_even_with_global_options(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--cwd", str(tmp_path), "unknown"])


def test_cli_clear_requires_confirmation_before_deleting_sessions(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    store.save(session)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    main(["clear", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert "Clear cancelled." in out
    summaries = store.list_sessions()
    assert [item.session_id for item in summaries] == [session.session_id]
