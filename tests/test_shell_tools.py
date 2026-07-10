from pathlib import Path
import threading
import time

from manus_mini.tools import ToolRegistry
from manus_mini.tools.shell_tools import CommandRisk
from manus_mini.tools.shell_tools import RunBashTool, RunTempScriptTool


class StaticRiskJudge:
    def __init__(self, requires_confirmation: bool) -> None:
        self.requires_confirmation = requires_confirmation
        self.calls: list[tuple[str, Path | None]] = []

    def analyze(self, command_text: str, workspace: Path | None) -> CommandRisk:
        self.calls.append((command_text, workspace))
        return CommandRisk(
            self.requires_confirmation,
            summary="llm says high risk" if self.requires_confirmation else "",
        )


def test_run_bash_executes_in_workspace_and_returns_output(tmp_path: Path) -> None:
    (tmp_path / "demo.txt").write_text("hello", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="pwd && cat demo.txt")

    assert result.ok is True
    assert result.data["exit_code"] == 0
    assert str(tmp_path) in result.content
    assert "hello" in result.content


def test_run_bash_reports_non_zero_exit_code(tmp_path: Path) -> None:
    result = RunBashTool().run(workspace=tmp_path, command="echo bad >&2; exit 7")

    assert result.ok is False
    assert result.error_code == "COMMAND_FAILED"
    assert result.data["exit_code"] == 7
    assert "bad" in result.data["stderr"]


def test_run_bash_timeout_terminates_child_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "child-finished.txt"

    result = RunBashTool().run(
        workspace=tmp_path,
        command=f"(sleep 2; touch {marker.name}) & wait",
        timeout_seconds=1,
        confirmed=True,
    )

    assert result.ok is False
    assert result.error_code == "COMMAND_TIMEOUT"
    time.sleep(0.2)
    assert not marker.exists()


def test_run_bash_honors_cooperative_cancellation(tmp_path: Path) -> None:
    cancel_event = threading.Event()
    marker = tmp_path / "cancelled-child-finished.txt"

    def cancel() -> None:
        time.sleep(0.1)
        cancel_event.set()

    thread = threading.Thread(target=cancel)
    thread.start()
    result = RunBashTool().run(
        workspace=tmp_path,
        command=f"(sleep 2; touch {marker.name}) & wait",
        _cancel_event=cancel_event,
        confirmed=True,
    )
    thread.join()

    assert result.ok is False
    assert result.error_code == "USER_CANCELLED"
    time.sleep(0.2)
    assert not marker.exists()


def test_run_bash_rejects_dangerous_commands(tmp_path: Path) -> None:
    result = RunBashTool().run(workspace=tmp_path, command="sudo rm -rf /")

    assert result.ok is False
    assert result.error_code == "COMMAND_REJECTED"
    assert "rejected" in result.summary


def test_run_bash_uses_llm_risk_judgement_for_confirmation(tmp_path: Path) -> None:
    judge = StaticRiskJudge(requires_confirmation=True)

    preview = RunBashTool(risk_judge=judge).preview(workspace=tmp_path, command="echo harmless")
    result = RunBashTool(risk_judge=judge).run(workspace=tmp_path, command="echo harmless")

    assert preview.requires_confirmation is True
    assert preview.summary == "llm says high risk"
    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert judge.calls


def test_run_bash_does_not_require_confirmation_only_because_path_is_external(tmp_path: Path) -> None:
    external_path = tmp_path.parent / "outside-marker.txt"
    judge = StaticRiskJudge(requires_confirmation=False)

    preview = RunBashTool(risk_judge=judge).preview(workspace=tmp_path, command=f"rm -f {external_path}")

    assert preview.requires_confirmation is False
    assert "outside workspace" not in preview.summary
    assert not external_path.exists()


def test_run_bash_allows_confirmed_high_risk_external_path(tmp_path: Path) -> None:
    external_path = tmp_path.parent / "outside-marker.txt"
    external_path.write_text("old", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command=f"rm -f {external_path}", confirmed=True)

    assert result.ok is True
    assert not external_path.exists()


def test_run_bash_uses_sanitized_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "secret-key")

    result = RunBashTool().run(workspace=tmp_path, command="env")

    assert result.ok is True
    assert "LLM_API_KEY" not in result.content


def test_run_bash_pathlib_write_bytes_requires_confirmation(tmp_path: Path) -> None:
    result = RunBashTool().run(
        workspace=tmp_path,
        command="python -c \"from pathlib import Path; Path('note.md').write_bytes(b'x')\"",
    )

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert not (tmp_path / "note.md").exists()


def test_run_bash_pathlib_open_write_requires_confirmation(tmp_path: Path) -> None:
    result = RunBashTool().run(
        workspace=tmp_path,
        command="python -c \"from pathlib import Path; Path('note.md').open('w').write('x')\"",
    )

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert not (tmp_path / "note.md").exists()


def test_run_bash_sensitive_file_read_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="cat .env")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_bash_grep_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / "private.pem").write_text("secret pem", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="grep secret private.pem")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "secret pem" not in result.content


def test_run_bash_nested_shell_sensitive_file_read_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="echo ok; bash -c 'echo nested; cat .env'")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_temp_script_nested_shell_sensitive_file_read_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env.test").write_text("LLM_API_KEY=test", encoding="utf-8")

    result = RunTempScriptTool().run(workspace=tmp_path, content="sh -c 'echo nested; head -n 1 .env.test'\n")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_bash_sensitive_input_redirection_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(
        workspace=tmp_path,
        command='python -c "import sys; print(sys.stdin.read())" < .env',
    )

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_temp_script_nested_sensitive_input_redirection_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env.test").write_text("LLM_API_KEY=test", encoding="utf-8")

    result = RunTempScriptTool().run(
        workspace=tmp_path,
        content="bash -c 'python -c \"import sys; print(sys.stdin.read())\" < .env.test'\n",
    )

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_bash_sensitive_command_substitution_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="echo $(cat .env)")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_temp_script_nested_sensitive_command_substitution_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env.test").write_text("LLM_API_KEY=test", encoding="utf-8")

    result = RunTempScriptTool().run(workspace=tmp_path, content="bash -c 'echo $(cat .env.test)'\n")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_bash_source_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="set -a; source .env; env")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_temp_script_dot_source_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env.test").write_text("LLM_API_KEY=test", encoding="utf-8")

    result = RunTempScriptTool().run(workspace=tmp_path, content="set -a\n. .env.test\nenv\n")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_bash_python_open_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="python -c \"print(open('.env').read())\"")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_bash_python_pathlib_read_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(
        workspace=tmp_path,
        command="python -c \"from pathlib import Path; print(Path('.env').read_text())\"",
    )

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_bash_python_pathlib_read_env_example_is_allowed(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("LLM_API_KEY=", encoding="utf-8")

    result = RunBashTool().run(
        workspace=tmp_path,
        command="python -c \"from pathlib import Path; print(Path('.env.example').read_text())\"",
    )

    assert result.ok is True
    assert "LLM_API_KEY=" in result.content


def test_run_temp_script_rejects_dangerous_content(tmp_path: Path) -> None:
    result = RunTempScriptTool().run(workspace=tmp_path, content="rm -rf /\n")

    assert result.ok is False
    assert result.tool_name == "run_temp_script"
    assert result.error_code == "COMMAND_REJECTED"


def test_run_temp_script_uses_llm_risk_judgement_for_confirmation(tmp_path: Path) -> None:
    judge = StaticRiskJudge(requires_confirmation=True)

    preview = RunTempScriptTool(risk_judge=judge).preview(workspace=tmp_path, content="echo script\n")
    result = RunTempScriptTool(risk_judge=judge).run(workspace=tmp_path, content="echo script\n")

    assert preview.requires_confirmation is True
    assert preview.summary == "llm says high risk"
    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"


def test_run_temp_script_sensitive_file_read_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env.test").write_text("LLM_API_KEY=test", encoding="utf-8")

    result = RunTempScriptTool().run(workspace=tmp_path, content="head -n 1 .env.test\n")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_temp_script_deletes_script_after_execution(tmp_path: Path) -> None:
    result = RunTempScriptTool().run(
        workspace=tmp_path,
        content="echo script:$PWD\n",
        filename="agent-check.sh",
    )

    assert result.ok is True
    assert result.data["exit_code"] == 0
    assert "script:" in result.content
    script_path = Path(result.data["script_path"])
    assert not script_path.exists()


def test_run_temp_script_deletes_script_after_failure(tmp_path: Path) -> None:
    result = RunTempScriptTool().run(
        workspace=tmp_path,
        content="echo fail >&2\nexit 3\n",
        filename="agent-failing-check.sh",
    )

    assert result.ok is False
    assert result.error_code == "COMMAND_FAILED"
    assert result.data["exit_code"] == 3
    assert "fail" in result.data["stderr"]
    assert not Path(result.data["script_path"]).exists()


def test_tool_registry_includes_shell_tools() -> None:
    registry = ToolRegistry()

    assert isinstance(registry.get("run_bash"), RunBashTool)
    assert isinstance(registry.get("run_temp_script"), RunTempScriptTool)
