from __future__ import annotations

import os
import re
import json
import shlex
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from manus_mini.llm import LLMClient
from manus_mini.models import Message
from manus_mini.redaction import redact_sensitive_text
from manus_mini.tools.base import BaseTool, ToolPreview, ToolResult


DEFAULT_OUTPUT_LIMIT = 12_000
FORBIDDEN_COMMAND_PATTERNS = (
    r"\bsudo\b",
    r"\brm\s+-rf\s+/",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bshutdown\b",
    r"\breboot\b",
)
WORKSPACE_MUTATION_COMMAND_PATTERNS = (
    r"\bsed\s+-i(?:\s|$)",
    r"\bperl\s+-pi(?:\s|$)",
    r"\bpython(?:3)?\b.*\bwrite_text\s*\(",
    r"\bpython(?:3)?\b.*\bwrite_bytes\s*\(",
    r"\bpython(?:3)?\b.*\bPath\s*\([^)]*\)\.open\s*\(\s*['\"][wa]",
    r"\bpython(?:3)?\b.*\bopen\s*\([^)]*,\s*['\"][wa]",
    r"\btee(?:\s+-a)?\s+(?!/)[A-Za-z0-9_.\-/]+",
    r"(^|[\s;&|])(?:>>|>)\s*(?!&|\d|/)[A-Za-z0-9_.\-/]+",
    r"(^|[;&|]\s*)printf\b[^|;&]*(>>|>\s*)[A-Za-z0-9_.\-/]+",
    r"(^|[;&|]\s*)echo\b[^|;&]*(>>|>\s*)[A-Za-z0-9_.\-/]+",
)
SENSITIVE_READ_COMMANDS = {".", "awk", "cat", "egrep", "fgrep", "grep", "head", "less", "more", "sed", "source", "tail"}
NESTED_SHELL_COMMANDS = {"bash", "sh", "zsh"}
COMMAND_RISK_SYSTEM_PROMPT = """You classify shell command risk before execution.
Return only compact JSON with:
- risk_level: "high" or "low"
- reason: short reason in the same language as the command if possible

Mark high risk only when this specific command/script is likely to cause destructive,
irreversible, privacy-sensitive, credential-exposing, privilege-changing, network-abusive,
or broad side-effect behavior. Do not mark high risk merely because a path is outside a
workspace."""


class RunBashTool(BaseTool):
    name = "run_bash"
    description = "Run a bash command in the workspace and return stdout, stderr, and exit code."
    risk_level = "command"
    is_read_only = False

    def __init__(self, risk_judge: CommandRiskJudge | None = None) -> None:
        self.risk_judge = risk_judge

    def preview(self, **kwargs: Any) -> ToolPreview:
        command = str(kwargs.get("command", "")).strip()
        workspace = _optional_workspace(kwargs)
        risk = analyze_command_risk(command, workspace=workspace, risk_judge=self.risk_judge)
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
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional command timeout in seconds. Omit for no timeout.",
                },
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
        risk = analyze_command_risk(command, workspace=workspace, risk_judge=self.risk_judge)
        if risk.requires_confirmation and not bool(kwargs.get("confirmed", False)):
            return _confirmation_required_result(self.name, risk)
        timeout_seconds = _optional_positive_int(kwargs.get("timeout_seconds"))
        output_limit = _positive_int(kwargs.get("output_limit"), DEFAULT_OUTPUT_LIMIT)
        return _run_command(
            tool_name=self.name,
            command=["bash", "-lc", command],
            cwd=workspace,
            timeout_seconds=timeout_seconds,
            output_limit=output_limit,
            cancel_event=kwargs.get("_cancel_event"),
        )


class RunTempScriptTool(BaseTool):
    name = "run_temp_script"
    description = "Write a temporary bash script, run it in the workspace, then delete the script."
    risk_level = "command"
    is_read_only = False

    def __init__(self, risk_judge: CommandRiskJudge | None = None) -> None:
        self.risk_judge = risk_judge

    def preview(self, **kwargs: Any) -> ToolPreview:
        content = "" if kwargs.get("content") is None else str(kwargs.get("content"))
        workspace = _optional_workspace(kwargs)
        risk = analyze_command_risk(content, workspace=workspace, risk_judge=self.risk_judge)
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
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional script timeout in seconds. Omit for no timeout.",
                },
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
        risk = analyze_command_risk(str(content), workspace=workspace, risk_judge=self.risk_judge)
        if risk.requires_confirmation and not bool(kwargs.get("confirmed", False)):
            return _confirmation_required_result(self.name, risk)
        timeout_seconds = _optional_positive_int(kwargs.get("timeout_seconds"))
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
                    cancel_event=kwargs.get("_cancel_event"),
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
    timeout_seconds: int | None,
    output_limit: int,
    cancel_event: threading.Event | None = None,
) -> ToolResult:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_safe_command_env(cwd),
        start_new_session=True,
    )
    started_at = time.monotonic()
    stdout = ""
    stderr = ""
    while True:
        if cancel_event is not None and cancel_event.is_set():
            stdout, stderr = _terminate_process_group(process)
            stdout = redact_sensitive_text(stdout or "")
            stderr = redact_sensitive_text(stderr or "")
            return ToolResult(
                tool_name=tool_name,
                ok=False,
                summary="command cancelled",
                content=_combined_output(stdout, stderr, output_limit),
                data={
                    "exit_code": process.returncode,
                    "stdout": _truncate(stdout, output_limit),
                    "stderr": _truncate(stderr, output_limit),
                    "timed_out": False,
                    "cancelled": True,
                },
                error_code="USER_CANCELLED",
            )
        if timeout_seconds is not None and time.monotonic() - started_at >= timeout_seconds:
            stdout, stderr = _terminate_process_group(process)
            stdout = redact_sensitive_text(stdout or "")
            stderr = redact_sensitive_text(stderr or "")
            return ToolResult(
                tool_name=tool_name,
                ok=False,
                summary=f"command timed out after {timeout_seconds}s",
                content=_combined_output(stdout, stderr, output_limit),
                data={
                    "exit_code": process.returncode,
                    "stdout": _truncate(stdout, output_limit),
                    "stderr": _truncate(stderr, output_limit),
                    "timed_out": True,
                    "cancelled": False,
                },
                error_code="COMMAND_TIMEOUT",
            )
        try:
            stdout, stderr = process.communicate(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            continue

    stdout = stdout or ""
    stderr = stderr or ""
    stdout = redact_sensitive_text(stdout)
    stderr = redact_sensitive_text(stderr)
    ok = process.returncode == 0
    return ToolResult(
        tool_name=tool_name,
        ok=ok,
        summary=f"command exited {process.returncode}",
        content=_combined_output(stdout, stderr, output_limit),
        data={
            "exit_code": process.returncode,
            "stdout": _truncate(stdout, output_limit),
            "stderr": _truncate(stderr, output_limit),
            "timed_out": False,
            "cancelled": False,
        },
        error_code=None if ok else "COMMAND_FAILED",
    )


def _terminate_process_group(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        return process.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stdout, stderr = process.communicate()
        return stdout or "", stderr or ""


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _optional_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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
    def __init__(
        self,
        requires_confirmation: bool,
        summary: str = "",
        paths: list[str] | None = None,
        source: str = "",
    ) -> None:
        self.requires_confirmation = requires_confirmation
        self.summary = summary
        self.paths = list(paths or [])
        self.source = source


class CommandRiskJudge(Protocol):
    def analyze(self, command_text: str, workspace: Path | None) -> CommandRisk:
        ...


class LLMCommandRiskJudge:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def analyze(self, command_text: str, workspace: Path | None) -> CommandRisk:
        normalized = command_text.strip()
        if not normalized:
            return CommandRisk(False, source="llm")
        request = {
            "workspace": str(workspace) if workspace is not None else "",
            "command_or_script": normalized,
        }
        result = self.llm.complete_with_tools(
            [
                Message.system(COMMAND_RISK_SYSTEM_PROMPT),
                Message.user(json.dumps(request, ensure_ascii=False)),
            ],
            tool_names=[],
        )
        parsed = _parse_llm_risk_content(result.content)
        if parsed is None:
            return CommandRisk(False, source="llm")
        risk_level, reason = parsed
        if risk_level != "high":
            return CommandRisk(False, source="llm")
        summary = f"LLM marked command as high risk: {reason}" if reason else "LLM marked command as high risk"
        return CommandRisk(True, summary=summary, source="llm")


def analyze_command_risk(
    command_text: str,
    workspace: Path | None,
    risk_judge: CommandRiskJudge | None = None,
) -> CommandRisk:
    normalized = command_text.strip()
    if not normalized:
        return CommandRisk(False)
    heuristic_risk = _analyze_local_command_mutation_risk(normalized)
    if heuristic_risk.requires_confirmation:
        return heuristic_risk
    if risk_judge is None:
        return CommandRisk(False)
    return risk_judge.analyze(normalized, workspace=workspace)


def _analyze_local_command_mutation_risk(command_text: str) -> CommandRisk:
    if _reads_sensitive_workspace_file(command_text):
        return CommandRisk(
            True,
            summary="command reads sensitive workspace files",
            source="local_heuristic",
        )
    if _touches_workspace_file(command_text):
        return CommandRisk(
            True,
            summary="command modifies workspace files: touch",
            source="local_heuristic",
        )
    for pattern in WORKSPACE_MUTATION_COMMAND_PATTERNS:
        if re.search(pattern, command_text):
            return CommandRisk(
                True,
                summary=f"command modifies workspace files: {pattern}",
                source="local_heuristic",
            )
    return CommandRisk(False, source="local_heuristic")


def _reads_sensitive_workspace_file(command_text: str, *, depth: int = 0) -> bool:
    if depth > 2:
        return False
    if _python_reads_sensitive_file(command_text):
        return True
    if _command_substitutions_read_sensitive_file(command_text, depth=depth):
        return True
    for tokens in _shell_command_segments(command_text):
        if not tokens:
            continue
        command_name = "." if tokens[0] == "." else Path(tokens[0]).name
        if command_name in NESTED_SHELL_COMMANDS and _nested_shell_reads_sensitive_file(tokens, depth=depth):
            return True
        if _has_sensitive_input_redirection(tokens):
            return True
        if command_name not in SENSITIVE_READ_COMMANDS:
            continue
        if any(_is_sensitive_shell_path(token) for token in tokens[1:]):
            return True
    return False


def _command_substitutions_read_sensitive_file(command_text: str, *, depth: int) -> bool:
    for nested_command in _command_substitution_contents(command_text):
        if _reads_sensitive_workspace_file(nested_command, depth=depth + 1):
            return True
    return False


def _command_substitution_contents(command_text: str) -> list[str]:
    contents: list[str] = []
    index = 0
    while index < len(command_text):
        if command_text.startswith("$(", index):
            content, next_index = _read_parenthesized_content(command_text, index + 2)
            if content is not None:
                contents.append(content)
                index = next_index
                continue
        if command_text[index] == "`":
            end_index = command_text.find("`", index + 1)
            if end_index != -1:
                contents.append(command_text[index + 1 : end_index])
                index = end_index + 1
                continue
        index += 1
    return contents


def _read_parenthesized_content(command_text: str, start_index: int) -> tuple[str | None, int]:
    depth = 1
    index = start_index
    while index < len(command_text):
        char = command_text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return command_text[start_index:index], index + 1
        index += 1
    return None, len(command_text)


def _shell_command_segments(command_text: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command_text.replace("\n", ";"), posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []

    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token and all(char in ";&|" for char in token):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _has_sensitive_input_redirection(tokens: list[str]) -> bool:
    for index, token in enumerate(tokens[:-1]):
        if token in {"<", "<>"} and _is_sensitive_shell_path(tokens[index + 1]):
            return True
    return False


def _nested_shell_reads_sensitive_file(tokens: list[str], *, depth: int) -> bool:
    for index, token in enumerate(tokens[1:], start=1):
        if token.startswith("-") and "c" in token:
            script_index = index + 1
            if script_index >= len(tokens):
                return False
            return _reads_sensitive_workspace_file(tokens[script_index], depth=depth + 1)
    return False


def _python_reads_sensitive_file(command_text: str) -> bool:
    if not re.search(r"\bpython(?:3)?\b", command_text):
        return False
    patterns = (
        r"\bopen\s*\(\s*['\"]([^'\"]+)['\"]",
        r"\bPath\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\.(?:read_text|read_bytes|open)\s*\(",
    )
    for pattern in patterns:
        if any(_is_sensitive_shell_path(match.group(1)) for match in re.finditer(pattern, command_text)):
            return True
    return False


def _is_sensitive_shell_path(token: str) -> bool:
    if token.startswith("-"):
        return False
    name = Path(token).name
    if name == ".env.example":
        return False
    if name.startswith(".env"):
        return True
    return Path(token).suffix.lower() in {".key", ".pem"}


def _touches_workspace_file(command_text: str) -> bool:
    if re.search(r"\bPath\s*\(\s*['\"](?!/)[^'\"]+['\"]\s*\)\.touch\s*\(", command_text):
        return True
    for match in re.finditer(r"(?:^|[;&|]\s*)touch\s+([^;&|\n]+)", command_text):
        try:
            tokens = shlex.split(match.group(1))
        except ValueError:
            continue
        if _touch_command_has_relative_path(tokens):
            return True
    return False


def _touch_command_has_relative_path(tokens: list[str]) -> bool:
    options_done = False
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if not options_done and token == "--":
            options_done = True
            continue
        if not options_done and token.startswith("-") and token != "-":
            if token in {"-A", "-d", "-r", "-t"}:
                skip_next = True
            continue
        if not token.startswith("/"):
            return True
    return False


def _parse_llm_risk_content(content: str) -> tuple[str, str] | None:
    text = content.strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match is None:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    risk_level = str(payload.get("risk_level") or payload.get("risk") or "").strip().lower()
    if risk_level not in {"high", "low"}:
        return None
    reason = str(payload.get("reason") or "").strip()
    return risk_level, reason


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
        data={"risk": "high", "risk_source": risk.source, "outside_workspace_paths": risk.paths},
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
