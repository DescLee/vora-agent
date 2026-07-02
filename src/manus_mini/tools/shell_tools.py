from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from manus_mini.tools.base import BaseTool, ToolResult


DEFAULT_COMMAND_TIMEOUT_SECONDS = 30
DEFAULT_OUTPUT_LIMIT = 12_000


class RunBashTool(BaseTool):
    name = "run_bash"
    description = "Run a bash command in the workspace and return stdout, stderr, and exit code."
    risk_level = "command"
    is_read_only = False

    def describe_preview(self, **kwargs: Any) -> str:
        command = str(kwargs.get("command", "")).strip()
        return f"Run bash command: {_short(command)}"

    def run(self, **kwargs: Any) -> ToolResult:
        command = str(kwargs.get("command", "")).strip()
        if not command:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="missing required argument: command",
                error_code="INVALID_TOOL_PARAMS",
            )
        workspace = Path(kwargs["workspace"]).expanduser().resolve()
        timeout_seconds = _positive_int(kwargs.get("timeout_seconds"), DEFAULT_COMMAND_TIMEOUT_SECONDS)
        output_limit = _positive_int(kwargs.get("output_limit"), DEFAULT_OUTPUT_LIMIT)
        return _run_command(
            tool_name=self.name,
            command=["bash", "-lc", command],
            cwd=workspace,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
        )


class RunTempScriptTool(BaseTool):
    name = "run_temp_script"
    description = "Write a temporary bash script, run it in the workspace, then delete the script."
    risk_level = "command"
    is_read_only = False

    def describe_preview(self, **kwargs: Any) -> str:
        filename = str(kwargs.get("filename") or "agent-script.sh")
        return f"Run temporary script: {filename}"

    def run(self, **kwargs: Any) -> ToolResult:
        content = kwargs.get("content")
        if content is None:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="missing required argument: content",
                error_code="INVALID_TOOL_PARAMS",
            )
        workspace = Path(kwargs["workspace"]).expanduser().resolve()
        timeout_seconds = _positive_int(kwargs.get("timeout_seconds"), DEFAULT_COMMAND_TIMEOUT_SECONDS)
        output_limit = _positive_int(kwargs.get("output_limit"), DEFAULT_OUTPUT_LIMIT)
        filename = _safe_script_filename(str(kwargs.get("filename") or "agent-script.sh"))
        script_path: Path | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="manus-mini-script-") as directory:
                script_path = Path(directory) / filename
                script_path.write_text(str(content), encoding=kwargs.get("encoding", "utf-8"))
                script_path.chmod(0o700)
                result = _run_command(
                    tool_name=self.name,
                    command=["bash", str(script_path)],
                    cwd=workspace,
                    timeout_seconds=timeout_seconds,
                    output_limit=output_limit,
                )
                result.data["script_path"] = str(script_path)
                return result
        finally:
            if script_path is not None:
                script_path.unlink(missing_ok=True)


def _run_command(
    tool_name: str,
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
    output_limit: int,
) -> ToolResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        stdout = _coerce_output(error.stdout)
        stderr = _coerce_output(error.stderr)
        return ToolResult(
            tool_name=tool_name,
            ok=False,
            summary=f"command timed out after {timeout_seconds}s",
            content=_combined_output(stdout, stderr, output_limit),
            data={
                "exit_code": None,
                "stdout": _truncate(stdout, output_limit),
                "stderr": _truncate(stderr, output_limit),
                "timed_out": True,
            },
            error_code="COMMAND_TIMEOUT",
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    ok = completed.returncode == 0
    return ToolResult(
        tool_name=tool_name,
        ok=ok,
        summary=f"command exited {completed.returncode}",
        content=_combined_output(stdout, stderr, output_limit),
        data={
            "exit_code": completed.returncode,
            "stdout": _truncate(stdout, output_limit),
            "stderr": _truncate(stderr, output_limit),
            "timed_out": False,
        },
        error_code=None if ok else "COMMAND_FAILED",
    )


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_script_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "agent-script.sh"
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in name)


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _combined_output(stdout: str, stderr: str, limit: int) -> str:
    parts = []
    if stdout:
        parts.append("stdout:\n" + _truncate(stdout, limit))
    if stderr:
        parts.append("stderr:\n" + _truncate(stderr, limit))
    return "\n\n".join(parts)


def _truncate(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    remaining = len(content) - limit
    return content[:limit] + f"\n... [truncated {remaining} more char(s)]"


def _short(content: str, limit: int = 120) -> str:
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "..."
