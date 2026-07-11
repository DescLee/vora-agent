import importlib
from pathlib import Path

import pytest

from vora.cli import main
from vora.models import LoopLimits, Message, SessionState, TaskState
from vora.session_store import SessionStore


def test_cli_list_prints_readable_session_table_without_opening_tui(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("上一轮问题"))
    session.messages.append(Message.agent("上一轮回答"))
    store.save(session)

    main(["list", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert f"Session directory: {store.sessions_dir}" in out
    assert "Saved sessions: 1" in out
    assert "SESSION ID" in out
    assert "UPDATED" in out
    assert "MESSAGES" in out
    assert "LAST USER MESSAGE" in out
    assert session.session_id in out
    assert "上一轮问题" in out
    assert f"Resume with: vora resume {session.session_id} --cwd {tmp_path}" in out
    assert f"Remove with: vora remove {session.session_id} --cwd {tmp_path}" in out
    assert f"Clear all with: vora clear --cwd {tmp_path}" in out


def test_package_exposes_python_module_entrypoint() -> None:
    module = importlib.import_module("vora.__main__")

    assert module.main is main


def test_cli_list_prints_session_directory_when_empty(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    main(["list", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert "No saved sessions." in out
    assert str(SessionStore(tmp_path).sessions_dir) in out
    assert "Saved sessions:" not in out
    assert f"Start with: vora run \"你的问题\" --cwd {tmp_path}" in out
    assert f"Example: vora run \"总结一下当前项目\" --cwd {tmp_path}" in out


def test_cli_doctor_prints_local_setup_without_leaking_api_key(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "LLM_PROVIDER=openai-compatible",
                "LLM_BASE_URL=http://localhost:1234/v1",
                "LLM_API_KEY=secret-doctor-key",
                "LLM_MODEL=demo-model",
            ]
        ),
        encoding="utf-8",
    )
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    store.save(session)

    main(["doctor", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert "Vora Doctor" in out
    assert f"CWD: {tmp_path}" in out
    assert str(store.sessions_dir) in out
    assert "(1 saved)" in out
    assert "Status: ready" in out
    assert "Base URL: http://localhost:1234/v1" in out
    assert "API key: configured" in out
    assert "secret-doctor-key" not in out
    assert "Model: demo-model" in out
    assert f'vora run "总结一下当前项目" --cwd {tmp_path}' in out


def test_cli_mcp_add_list_remove_project_server(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    main(["mcp", "add", "filesystem", "--command", "npx", "--arg", "-y", "--arg", "@modelcontextprotocol/server-filesystem", "--cwd", str(tmp_path)])
    add_out = capsys.readouterr().out
    assert "MCP server 'filesystem' added" in add_out

    main(["mcp", "list", "--cwd", str(tmp_path)])
    list_out = capsys.readouterr().out
    assert "MCP config:" in list_out
    assert "filesystem" in list_out
    assert "npx -y @modelcontextprotocol/server-filesystem" in list_out

    main(["mcp", "remove", "filesystem", "--cwd", str(tmp_path)])
    remove_out = capsys.readouterr().out
    assert "MCP server 'filesystem' removed" in remove_out

    main(["mcp", "list", "--cwd", str(tmp_path)])
    empty_out = capsys.readouterr().out
    assert "No MCP servers configured." in empty_out


def test_cli_skills_add_list_remove_project_skill(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    source = tmp_path / "source-skill"
    source.mkdir()
    (source / "skill.json").write_text(
        '{"name":"project-analysis","description":"demo","triggers":["分析"],"tool_allowlist":["read_file"]}',
        encoding="utf-8",
    )
    (source / "instructions.md").write_text("按 demo Skill 执行。", encoding="utf-8")

    main(["skills", "add", str(source), "--cwd", str(tmp_path)])
    add_out = capsys.readouterr().out
    assert "Skill 'project-analysis' added" in add_out

    main(["skills", "list", "--cwd", str(tmp_path)])
    list_out = capsys.readouterr().out
    assert "Skills:" in list_out
    assert "project-analysis" in list_out
    assert "project" in list_out
    assert "demo" in list_out

    main(["skills", "remove", "project-analysis", "--cwd", str(tmp_path)])
    remove_out = capsys.readouterr().out
    assert "Skill 'project-analysis' removed" in remove_out
    assert not (tmp_path / "skills" / "project-analysis").exists()


def test_cli_run_creates_session_prints_result_and_resume_command(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    seen = {}

    class FakeRuntime:
        def __init__(self, *, default_limits, dry_run, cwd):  # noqa: ANN001
            seen["max_react_iterations"] = default_limits.max_react_iterations
            seen["dry_run"] = dry_run
            seen["cwd"] = cwd

        def on_user_message(self, content: str, session: SessionState) -> SessionState:
            seen["content"] = content
            session.messages.append(Message.user(content))
            task = TaskState.create(goal=content, cwd=session.cwd)
            task.status = "done"
            task.result = "一次性回答"
            session.active_task = task
            session.messages.append(Message.agent("一次性回答"))
            return session

    monkeypatch.setattr("vora.cli.AgentRuntime", FakeRuntime)

    main(["run", "总结当前项目", "--cwd", str(tmp_path), "--dry-run", "--max-react", "1"])

    out = capsys.readouterr().out
    sessions = SessionStore(tmp_path).list_sessions()
    assert seen == {
        "max_react_iterations": 1,
        "dry_run": True,
        "cwd": tmp_path,
        "content": "总结当前项目",
    }
    assert len(sessions) == 1
    assert "一次性回答" in out
    assert f"Session ID: {sessions[0].session_id}" in out
    assert f"Resume with: vora resume {sessions[0].session_id} --cwd {tmp_path}" in out


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
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    store.save(session)
    seen = {}

    def fake_run(self):  # noqa: ANN001
        seen["initial_session"] = self.manager.current.session_id

    monkeypatch.setattr("vora.prompt_tui.PromptTui.run", fake_run)

    main(["resume", session.session_id, "--cwd", str(tmp_path)])

    assert seen["initial_session"] == session.session_id


def test_cli_resume_honors_global_dry_run_and_limit_overrides(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    store.save(session)
    seen = {}

    def fake_run(self):  # noqa: ANN001
        seen["initial_session"] = self.manager.current.session_id
        seen["dry_run"] = self.manager.runtime.dry_run
        seen["max_react_iterations"] = self.manager.runtime.default_limits.max_react_iterations

    monkeypatch.setattr("vora.prompt_tui.PromptTui.run", fake_run)

    main(["--dry-run", "--max-react", "1", "resume", session.session_id, "--cwd", str(tmp_path)])

    assert seen == {
        "initial_session": session.session_id,
        "dry_run": True,
        "max_react_iterations": 1,
    }


def test_cli_resume_accepts_runtime_overrides_after_session_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    store.save(session)
    seen = {}

    def fake_run(self):  # noqa: ANN001
        seen["dry_run"] = self.manager.runtime.dry_run
        seen["max_engineering_steps"] = self.manager.runtime.default_limits.max_engineering_steps
        seen["max_react_iterations"] = self.manager.runtime.default_limits.max_react_iterations

    monkeypatch.setattr("vora.prompt_tui.PromptTui.run", fake_run)

    main(["resume", session.session_id, "--cwd", str(tmp_path), "--dry-run", "--max-steps", "2", "--max-react", "1"])

    assert seen == {
        "dry_run": True,
        "max_engineering_steps": 2,
        "max_react_iterations": 1,
    }


def test_cli_resume_preserves_saved_active_task_limits_without_overrides(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    session.active_task = TaskState.create(goal="继续任务", cwd=tmp_path)
    session.active_task.limits = LoopLimits(max_react_iterations=7)
    store.save(session)
    seen = {}

    def fake_run(self):  # noqa: ANN001
        seen["max_react_iterations"] = self.manager.runtime.default_limits.max_react_iterations

    monkeypatch.setattr("vora.prompt_tui.PromptTui.run", fake_run)

    main(["resume", session.session_id, "--cwd", str(tmp_path)])

    assert seen["max_react_iterations"] == 7


def test_cli_resume_missing_session_prints_friendly_error(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    with pytest.raises(SystemExit) as error:
        main(["resume", "missing-session", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert error.value.code == 1
    assert "Error: session 'missing-session' not found." in out
    assert f"List sessions with: vora list --cwd {tmp_path}" in out


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


def test_cli_resume_prints_friendly_error_when_terminal_is_unavailable(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    store.save(session)

    def raise_terminal_error(self):  # noqa: ANN001
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr("vora.prompt_tui.PromptTui.run", raise_terminal_error)

    with pytest.raises(SystemExit) as error:
        main(["resume", session.session_id, "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert error.value.code == 1
    assert "Error: interactive terminal UI requires a terminal." in out


def test_cli_remove_invalid_session_id_prints_friendly_error(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    with pytest.raises(SystemExit) as error:
        main(["remove", "../sessions", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert error.value.code == 1
    assert "Error: invalid session id '../sessions'." in out


def test_cli_remove_missing_session_suggests_listing_sessions(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    with pytest.raises(SystemExit) as error:
        main(["remove", "missing-session", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert error.value.code == 1
    assert "Error: session 'missing-session' not found." in out
    assert f"List sessions with: vora list --cwd {tmp_path}" in out


def test_cli_rejects_removed_tui_subcommand(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["tui", "--cwd", str(tmp_path)])

    err = capsys.readouterr().err
    assert error.value.code == 2
    assert "invalid choice" in err
    assert "{list,doctor,mcp,skills,run,resume,remove,clear}" in err


def test_cli_without_command_opens_tui(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    seen = {}

    def fake_run(self):  # noqa: ANN001
        seen["cwd"] = self.manager.current.cwd
        seen["dry_run"] = self.manager.runtime.dry_run
        seen["max_react_iterations"] = self.manager.runtime.default_limits.max_react_iterations

    monkeypatch.setattr("vora.prompt_tui.PromptTui.run", fake_run)

    main(["--cwd", str(tmp_path), "--dry-run", "--max-react", "1"])

    assert seen == {
        "cwd": tmp_path,
        "dry_run": True,
        "max_react_iterations": 1,
    }


def test_cli_subcommands_preserve_global_cwd_before_command(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    project = tmp_path / "project"
    project.mkdir()
    store = SessionStore(project)
    session = SessionState.create(cwd=project)
    session.messages.append(Message.user("来自全局 cwd"))
    store.save(session)

    main(["--cwd", str(project), "list"])

    out = capsys.readouterr().out
    assert session.session_id in out
    assert "来自全局 cwd" in out


def test_cli_rejects_unknown_subcommand_even_with_global_options(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--cwd", str(tmp_path), "unknown"])


def test_cli_help_describes_global_options_and_defaults(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    out = capsys.readouterr().out
    assert error.value.code == 0
    assert "Self-managed coding agent runtime" in out
    assert "working directory" in out
    assert "doctor" in out
    assert "mcp" in out
    assert "skills" in out
    assert "run" in out
    assert "preview tool execution without side effects" in out
    assert "engineering loop limit" in out
    assert "ReAct iteration limit" in out
    assert "reflection loop limit" in out
    assert "tool retry limit" in out
    assert "Interactive mode: vora --cwd ." in out
    assert 'Example: vora run "总结一下当前项目" --cwd .' in out
    assert "Then resume with: vora resume <session_id> --cwd ." in out
    assert "(default: 3)" in out
    assert "(default: 99)" in out
    assert "tui" not in out


def test_cli_help_does_not_expose_tui_as_command_or_concept(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    out = capsys.readouterr().out.lower()
    assert error.value.code == 0
    assert "tui" not in out
    assert "terminal ui" not in out


def test_cli_subcommand_help_describes_cwd_and_force_options(capsys) -> None:
    with pytest.raises(SystemExit) as list_error:
        main(["list", "--help"])
    list_out = capsys.readouterr().out
    assert list_error.value.code == 0
    assert "working directory" in list_out

    with pytest.raises(SystemExit) as clear_error:
        main(["clear", "--help"])
    clear_out = capsys.readouterr().out
    assert clear_error.value.code == 0
    assert "working directory" in clear_out
    assert "skip confirmation prompt" in clear_out

    with pytest.raises(SystemExit) as doctor_error:
        main(["doctor", "--help"])
    doctor_out = capsys.readouterr().out
    assert doctor_error.value.code == 0
    assert "check local setup and storage paths" in doctor_out
    assert "does not call the LLM API" in doctor_out

    with pytest.raises(SystemExit) as mcp_error:
        main(["mcp", "--help"])
    mcp_out = capsys.readouterr().out
    assert mcp_error.value.code == 0
    assert "manage MCP server configs" in mcp_out
    assert "{list,add,remove}" in mcp_out

    with pytest.raises(SystemExit) as skills_error:
        main(["skills", "--help"])
    skills_out = capsys.readouterr().out
    assert skills_error.value.code == 0
    assert "manage local Skills" in skills_out
    assert "{list,add,remove}" in skills_out


def test_cli_remove_help_describes_session_id_and_risk(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["remove", "--help"])

    out = capsys.readouterr().out
    assert error.value.code == 0
    assert "saved session id to remove" in out
    assert "also removes matching log directories" in out
    assert "Example: vora remove <session_id> --cwd ." in out


def test_cli_clear_help_describes_scope_and_force(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["clear", "--help"])

    out = capsys.readouterr().out
    assert error.value.code == 0
    assert "clear all saved sessions for the working directory" in out
    assert "also removes matching log directories" in out
    assert "Use --force only in scripts" in out


def test_cli_run_help_describes_prompt_and_examples(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["run", "--help"])

    out = capsys.readouterr().out
    assert error.value.code == 0
    assert "prompt text to execute once" in out
    assert 'Example: vora run "总结一下当前项目" --cwd .' in out
    assert "Quote multi-word prompts" in out


def test_cli_run_missing_prompt_prints_actionable_example(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["run"])

    err = capsys.readouterr().err
    assert error.value.code == 2
    assert "the following arguments are required: prompt" in err
    assert 'Example: vora run "总结一下当前项目" --cwd .' in err


def test_cli_run_empty_prompt_prints_actionable_example(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["run", "", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert error.value.code == 1
    assert "Error: prompt is required." in out
    assert f'Example: vora run "总结一下当前项目" --cwd {tmp_path}' in out


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--max-steps", "0"),
        ("--max-react", "-1"),
        ("--max-reflect", "0"),
        ("--max-tool-retries", "-1"),
    ],
)
def test_cli_rejects_non_positive_loop_limit_options(option: str, value: str, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        main([option, value, "list", "--cwd", str(tmp_path)])

    assert error.value.code == 2


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


def test_cli_clear_treats_missing_stdin_as_cancelled(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    store = SessionStore(tmp_path)
    session = SessionState.create(cwd=tmp_path)
    store.save(session)

    def raise_eof(prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    main(["clear", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert "Clear cancelled." in out
    assert [item.session_id for item in store.list_sessions()] == [session.session_id]


def test_cli_clear_empty_sessions_suggests_starting_or_listing(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    main(["clear", "--cwd", str(tmp_path)])

    out = capsys.readouterr().out
    assert "No saved sessions to clear." in out
    assert f"Start with: vora run \"你的问题\" --cwd {tmp_path}" in out
    assert f"List sessions with: vora list --cwd {tmp_path}" in out
