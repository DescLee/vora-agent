from pathlib import Path
import json
import threading
import time

from vora.llm import LLMResult
from vora.tools.shell_tools import LLMCommandRiskJudge
from vora.tools import ToolRegistry
from vora.tools.shell_tools import CommandRisk
from vora.tools.shell_tools import RunBashTool, RunTempScriptTool


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


class CapturingRiskLLM:
    def __init__(self) -> None:
        self.messages = []

    def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
        self.messages = messages
        return LLMResult(content='{"risk_level":"low","reason":"safe"}')


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


def test_llm_command_risk_judge_redacts_sensitive_command_values(tmp_path: Path) -> None:
    llm = CapturingRiskLLM()

    result = LLMCommandRiskJudge(llm).analyze("echo CLIENT_SECRET=plain-secret", workspace=tmp_path)

    assert result.requires_confirmation is False
    user_payload = json.loads(llm.messages[-1].content)
    assert user_payload["command_or_script"] == "echo CLIENT_SECRET=[REDACTED]"
    assert "plain-secret" not in llm.messages[-1].content


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


def test_run_bash_redacts_sensitive_values_from_output(tmp_path: Path) -> None:
    token = "sk-shell-output-secret"
    url = "https://example.test/callback?access_token=plain-secret&ok=1"

    result = RunBashTool().run(
        workspace=tmp_path,
        command=f"printf 'Authorization: Bearer {token}\\n'; printf '{url}\\n' >&2",
    )

    assert result.ok is True
    assert token not in result.content
    assert "plain-secret" not in result.content
    assert token not in result.data["stdout"]
    assert "plain-secret" not in result.data["stderr"]
    assert "Authorization: Bearer [REDACTED]" in result.content
    assert "access_token=[REDACTED]" in result.data["stderr"]


def test_run_bash_preview_redacts_sensitive_values(tmp_path: Path) -> None:
    preview = RunBashTool().preview(
        workspace=tmp_path,
        command="echo CLIENT_SECRET=plain-secret",
    )

    assert "plain-secret" not in preview.summary
    assert "plain-secret" not in preview.args["command"]
    assert "CLIENT_SECRET=[REDACTED]" in preview.summary
    assert preview.args["command"] == "echo CLIENT_SECRET=[REDACTED]"


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


def test_run_bash_copy_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="cp .env leaked.txt")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert not (tmp_path / "leaked.txt").exists()


def test_run_temp_script_copy_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env.test").write_text("LLM_API_KEY=test", encoding="utf-8")

    result = RunTempScriptTool().run(workspace=tmp_path, content="cp .env.test leaked.txt\n")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert not (tmp_path / "leaked.txt").exists()


def test_run_bash_move_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="mv .env leaked.txt")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert (tmp_path / ".env").exists()
    assert not (tmp_path / "leaked.txt").exists()


def test_run_bash_install_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / "private.pem").write_text("pem-secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="install private.pem copied.pem")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert not (tmp_path / "copied.pem").exists()


def test_run_bash_tar_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="tar -cf leaked.tar .env")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert not (tmp_path / "leaked.tar").exists()


def test_run_bash_rsync_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="rsync .env leaked.txt")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert not (tmp_path / "leaked.txt").exists()


def test_run_bash_zip_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    preview = RunBashTool().preview(workspace=tmp_path, command="zip leaked.zip .env")

    assert preview.requires_confirmation is True
    assert "sensitive workspace files" in preview.summary


def test_run_bash_gzip_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    preview = RunBashTool().preview(workspace=tmp_path, command="gzip .env")

    assert preview.requires_confirmation is True
    assert "sensitive workspace files" in preview.summary


def test_run_bash_base64_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    preview = RunBashTool().preview(workspace=tmp_path, command="base64 .env")

    assert preview.requires_confirmation is True
    assert "sensitive workspace files" in preview.summary


def test_run_bash_wc_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="wc -c .env")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"


def test_run_bash_openssl_input_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    preview = RunBashTool().preview(workspace=tmp_path, command="openssl base64 -in .env")

    assert preview.requires_confirmation is True
    assert "sensitive workspace files" in preview.summary


def test_run_bash_python_shutil_copy_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(
        workspace=tmp_path,
        command="python -c \"import shutil; shutil.copyfile('.env', 'leaked.txt')\"",
    )

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert not (tmp_path / "leaked.txt").exists()


def test_run_bash_python_variable_open_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(workspace=tmp_path, command="python -c \"p='.env'; print(open(p).read())\"")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_bash_python_variable_pathlib_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")

    result = RunBashTool().run(
        workspace=tmp_path,
        command="python -c \"from pathlib import Path; p=Path('.env'); print(p.read_text())\"",
    )

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert "LLM_API_KEY" not in result.content


def test_run_temp_script_base64_sensitive_file_requires_confirmation(tmp_path: Path) -> None:
    (tmp_path / ".env.test").write_text("LLM_API_KEY=test", encoding="utf-8")

    preview = RunTempScriptTool().preview(workspace=tmp_path, content="base64 .env.test\n")

    assert preview.requires_confirmation is True
    assert "sensitive workspace files" in preview.summary


def test_run_bash_fd_redirection_to_workspace_file_requires_confirmation(tmp_path: Path) -> None:
    result = RunBashTool().run(workspace=tmp_path, command="python -c 'print(123)' 1>out.txt")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert not (tmp_path / "out.txt").exists()


def test_run_bash_stderr_redirection_to_workspace_file_requires_confirmation(tmp_path: Path) -> None:
    result = RunBashTool().run(workspace=tmp_path, command="python -c 'import sys; print(123, file=sys.stderr)' 2>err.log")

    assert result.ok is False
    assert result.error_code == "COMMAND_REQUIRES_CONFIRMATION"
    assert not (tmp_path / "err.log").exists()


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
