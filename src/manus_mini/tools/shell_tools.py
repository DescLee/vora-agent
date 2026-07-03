from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from manus_mini.tools.base import BaseTool, ToolPreview, ToolResult


DEFAULT_COMMAND_TIMEOUT_SECONDS = 30
DEFAULT_OUTPUT_LIMIT = 12_000
FORBIDDEN_COMMAND_PATTERNS = (
    r"\bsudo\b",
    r"\brm\s+-rf\s+/",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bshutdown\b",
    r"\breboot\b",
)
HIGH_RISK_MUTATING_COMMANDS = {
    "chmod",
    "chown",
    "cp",
    "install",
    "ln",
    "mkdir",
    "mv",
    "rm",
    "rsync",
    "sed",
    "tee",
    "touch",
    "truncate",
}
SHELL_REDIRECT_PATTERN = re.compile(r"(?:^|[\s;&|])(?:>>?|2>>?|&>|2>)\s*(?P<path>(?:~|/)[^\s;&|]+)")
ABSOLUTE_PATH_PATTERN = re.compile(r"(?<![\w.-])(?:~|/)[^\s;&|)]+")


class RunBashTool(BaseTool):
    name = "run_bash"
    description = "Run a bash command in the workspace and return stdout, stderr, and exit code."
    risk_level = "command"
    is_read_only = False

    def preview(self, **kwargs: Any) -> ToolPreview:
        command = str(kwargs.get("command", "")).strip()
        workspace = _optional_workspace(kwargs)
        risk = analyze_command_risk(command, workspace=workspace)
        return ToolPreview(
            tool_name=self.name,
            summary=risk.summary or self.describe_preview(**kwargs),
            risk_level=self.risk_level,
            requires_confirmation=risk.requires_confirmation,
            args=dict(kwargs),
        )

    def describe_preview(self, **kwargs: Any) -> str:
        command = str(kwargs.get("command", "")).strip()
        return f"Run bash command: {_short(command)}"

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Bash command to run from the workspace root."},
                "timeout_seconds": {"type": "integer", "default": 30, "minimum": 1, "maximum": 300},
                "output_limit": {"type": "integer", "default": 12000, "minimum": 1000, "maximum": 50000},
            },
            "required": ["command"],
            "additionalProperties": False,
        }

    def run(self, **kwargs: Any) -> ToolResult:
        command = str(kwargs.get("command", "")).strip()
        if not command:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="missing required argument: command",
                error_code="INVALID_TOOL_PARAMS",
            )
        rejection = _command_rejection(command, tool_name=self.name)
        if rejection is not None:
            return rejection
        workspace = Path(kwargs["workspace"]).expanduser().resolve()
        risk = analyze_command_risk(command, workspace=workspace)
        if risk.requires_confirmation and not bool(kwargs.get("confirmed", False)):
            return _confirmation_required_result(self.name, risk)
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

    def preview(self, **kwargs: Any) -> ToolPreview:
        content = "" if kwargs.get("content") is None else str(kwargs.get("content"))
        workspace = _optional_workspace(kwargs)
        risk = analyze_command_risk(content, workspace=workspace)
        return ToolPreview(
            tool_name=self.name,
            summary=risk.summary or self.describe_preview(**kwargs),
            risk_level=self.risk_level,
            requires_confirmation=risk.requires_confirmation,
            args=dict(kwargs),
        )

    def describe_preview(self, **kwargs: Any) -> str:
        filename = str(kwargs.get("filename") or "agent-script.sh")
        return f"Run temporary script: {filename}"

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Bash script content. The script is deleted after execution."},
                "filename": {"type": "string", "default": "agent-check.sh"},
                "is_test": {
                    "type": "boolean",
                    "description": "Set true when the temporary script is a validation test gate for code changes.",
                    "default": False,
                },
                "timeout_seconds": {"type": "integer", "default": 30, "minimum": 1, "maximum": 300},
                "output_limit": {"type": "integer", "default": 12000, "minimum": 1000, "maximum": 50000},
            },
            "required": ["content"],
            "additionalProperties": False,
        }

    def run(self, **kwargs: Any) -> ToolResult:
        content = kwargs.get("content")
        if content is None:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="missing required argument: content",
                error_code="INVALID_TOOL_PARAMS",
            )
        rejection = _command_rejection(str(content), tool_name=self.name)
        if rejection is not None:
            return rejection
        workspace = Path(kwargs["workspace"]).expanduser().resolve()
        risk = analyze_command_risk(str(content), workspace=workspace)
        if risk.requires_confirmation and not bool(kwargs.get("confirmed", False)):
            return _confirmation_required_result(self.name, risk)
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
            env=_safe_command_env(cwd),
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


def _command_rejection(command_text: str, tool_name: str) -> ToolResult | None:
    normalized = command_text.strip()
    for pattern in FORBIDDEN_COMMAND_PATTERNS:
        if re.search(pattern, normalized):
            return ToolResult(
                tool_name=tool_name,
                ok=False,
                summary=f"command rejected by safety policy: {pattern}",
                error_code="COMMAND_REJECTED",
                data={"pattern": pattern},
            )
    return None


class CommandRisk:
    def __init__(self, requires_confirmation: bool, summary: str = "", paths: list[str] | None = None) -> None:
        self.requires_confirmation = requires_confirmation
        self.summary = summary
        self.paths = list(paths or [])


def analyze_command_risk(command_text: str, workspace: Path | None) -> CommandRisk:
    if workspace is None:
        return CommandRisk(False)
    normalized = command_text.strip()
    if not normalized:
        return CommandRisk(False)
    if not _looks_mutating(normalized):
        return CommandRisk(False)
    outside_paths = _outside_workspace_paths(normalized, workspace)
    if not outside_paths:
        return CommandRisk(False)
    visible_paths = ", ".join(outside_paths[:3])
    extra = len(outside_paths) - 3
    if extra > 0:
        visible_paths += f", ... (+{extra})"
    return CommandRisk(
        True,
        summary=f"high-risk command may modify outside workspace: {visible_paths}",
        paths=outside_paths,
    )


def _looks_mutating(command_text: str) -> bool:
    if SHELL_REDIRECT_PATTERN.search(command_text):
        return True
    try:
        tokens = shlex.split(command_text, posix=True)
    except ValueError:
        tokens = re.split(r"\s+", command_text)
    for token in tokens:
        command_name = Path(token).name
        if command_name in HIGH_RISK_MUTATING_COMMANDS:
            return True
    return False


def _outside_workspace_paths(command_text: str, workspace: Path) -> list[str]:
    workspace_root = workspace.expanduser().resolve()
    paths: list[str] = []
    for raw_path in _absolute_path_candidates(command_text):
        resolved = Path(raw_path).expanduser().resolve(strict=False)
        try:
            resolved.relative_to(workspace_root)
        except ValueError:
            value = str(resolved)
            if value not in paths:
                paths.append(value)
    return paths


def _absolute_path_candidates(command_text: str) -> list[str]:
    candidates = [match.group("path") for match in SHELL_REDIRECT_PATTERN.finditer(command_text)]
    candidates.extend(match.group(0) for match in ABSOLUTE_PATH_PATTERN.finditer(command_text))
    cleaned: list[str] = []
    for candidate in candidates:
        value = candidate.strip().strip("'\"")
        if value and value not in cleaned:
            cleaned.append(value)
    return cleaned


def _optional_workspace(kwargs: dict[str, Any]) -> Path | None:
    workspace = kwargs.get("workspace")
    if workspace is None:
        return None
    return Path(workspace).expanduser().resolve()


def _confirmation_required_result(tool_name: str, risk: CommandRisk) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        ok=False,
        summary=risk.summary or "command requires confirmation",
        error_code="COMMAND_REQUIRES_CONFIRMATION",
        data={"risk": "high", "outside_workspace_paths": risk.paths},
    )


def _safe_command_env(cwd: Path) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": str(cwd),
        "PWD": str(cwd),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", os.environ.get("LANG", "C.UTF-8")),
    }
    pythonpath = os.environ.get("PYTHONPATH")
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    return env


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
