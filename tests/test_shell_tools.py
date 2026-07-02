from pathlib import Path

from manus_mini.tools import ToolRegistry
from manus_mini.tools.shell_tools import RunBashTool, RunTempScriptTool


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
