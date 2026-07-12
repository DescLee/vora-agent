from __future__ import annotations

import os
import re
import tempfile
from collections import deque
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from vora.tools.base import BaseTool, ToolPreview, ToolResult, resolve_workspace_path


DEFAULT_LIST_LIMIT = 500
DEFAULT_MAX_READ_BYTES = 128_000
DEFAULT_MAX_WRITE_BYTES = 1_000_000
DEFAULT_READ_LIMIT_LINES = 200
DEFAULT_READ_CONTEXT_LINES = 40
DEFAULT_READ_MAX_MATCHES = 3
DEFAULT_LARGE_FILE_PREVIEW_LINES = 2
DEFAULT_LARGE_FILE_PREVIEW_CHARS = 1_200
GENERATED_DATA_FILE_SUMMARY_BYTES = 100_000
HARD_LARGE_FILE_SUMMARY_BYTES = 500_000
FULL_REWRITE_WARNING_BYTES = 4_096
NOISE_DIR_NAMES = {
    ".cache",
    ".dart_tool",
    ".git",
    ".gradle",
    ".hg",
    ".idea",
    ".manus",
    ".vora",
    ".mvn",
    ".mypy_cache",
    ".next",
    ".nox",
    ".nuxt",
    ".parcel-cache",
    ".pytest_cache",
    ".pyre",
    ".ruff_cache",
    ".serverless",
    ".svn",
    ".terraform",
    ".tox",
    ".turbo",
    ".venv",
    ".vscode",
    "__pycache__",
    "bin",
    "bower_components",
    "build",
    "coverage",
    "debug",
    "dist",
    "env",
    "htmlcov",
    "logs",
    "node_modules",
    "obj",
    "out",
    "outputs",
    "pkg",
    "runs",
    "target",
    "tmp",
    "vendor",
    "venv",
}
SAFE_ENV_TEMPLATE_FILE_NAMES = {".env.example"}


class ListFilesTool(BaseTool):
    name = "list_files"
    description = "List files under the workspace using relative paths."
    risk_level = "safe"
    is_read_only = True

    def describe_preview(self, **kwargs: Any) -> str:
        workspace = Path(kwargs["workspace"])
        return f"List files in {workspace}"

    def preview(self, **kwargs) -> ToolPreview:
        return super().preview(**kwargs)

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory or file path inside the workspace. Use '.' for project root.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of paths to return.",
                    "default": 500,
                    "minimum": 1,
                    "maximum": 2000,
                },
            },
            "additionalProperties": False,
        }

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path", ".")
        limit = _positive_int(kwargs.get("limit"), DEFAULT_LIST_LIMIT)
        try:
            root = _resolve_tool_path(self.name, workspace, path)
        except _ToolPathError as error:
            return error.result
        if not root.exists():
            return ToolResult(tool_name=self.name, ok=False, summary="workspace not found", error_code="FILE_NOT_FOUND")

        workspace_root = workspace.expanduser().resolve()
        gitignore_rules = load_gitignore_rules(workspace_root)

        if root.is_file():
            if (
                not _is_path_within(root, workspace_root)
                or is_gitignored(root, workspace_root, gitignore_rules)
                or _is_sensitive_file_path(_display_path(root, workspace_root))
            ):
                files = []
            else:
                files = [root]
        else:
            files = [
                item
                for item in sorted(root.rglob("*"))
                if item.is_file()
                and _is_path_within(item, workspace_root)
                and not _is_noise_path(item, root)
                and not is_gitignored(item, workspace_root, gitignore_rules)
                and not _is_sensitive_file_path(_display_path(item, workspace_root))
            ]

        paths = [_display_path(item, workspace_root) for item in files]
        truncated = len(paths) > limit
        visible_paths = paths[:limit]
        return ToolResult(
            tool_name=self.name,
            ok=True,
            summary=f"found {len(paths)} files" + (" (truncated)" if truncated else ""),
            paths=visible_paths,
            data={"total": len(paths), "limit": limit, "truncated": truncated},
        )

    def resource_keys(self, **kwargs: Any) -> list[str]:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path", ".")
        root = resolve_workspace_path(workspace, path)
        return [_display_path(root, workspace.expanduser().resolve())]


class GlobTool(BaseTool):
    name = "glob"
    description = "Find workspace files matching a glob pattern. Prefer this over repeated list_files calls when locating files."
    risk_level = "safe"
    is_read_only = True

    def describe_preview(self, **kwargs: Any) -> str:
        pattern = kwargs.get("pattern", "*")
        return f"Find files matching {pattern}"

    def preview(self, **kwargs) -> ToolPreview:
        return super().preview(**kwargs)

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern relative to the workspace, e.g. 'src/**/*.ts'.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional relative directory to search from. Defaults to project root.",
                    "default": ".",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of matching paths to return.",
                    "default": 500,
                    "minimum": 1,
                    "maximum": 2000,
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        pattern = str(kwargs.get("pattern") or "").strip()
        search_path = kwargs.get("path", ".")
        limit = _positive_int(kwargs.get("limit"), DEFAULT_LIST_LIMIT)
        if not pattern:
            return ToolResult(tool_name=self.name, ok=False, summary="missing required argument: pattern", error_code="INVALID_TOOL_PARAMS")
        if Path(pattern).is_absolute():
            return ToolResult(tool_name=self.name, ok=False, summary="pattern must be relative to workspace", error_code="INVALID_TOOL_PARAMS")
        try:
            root = _resolve_tool_path(self.name, workspace, search_path)
        except _ToolPathError as error:
            return error.result
        if not root.exists():
            return ToolResult(tool_name=self.name, ok=False, summary="workspace not found", error_code="FILE_NOT_FOUND")
        if not root.is_dir():
            return ToolResult(tool_name=self.name, ok=False, summary="path is not a directory", error_code="INVALID_TOOL_PARAMS")

        workspace_root = workspace.expanduser().resolve()
        gitignore_rules = load_gitignore_rules(workspace_root)
        files = [
            item
            for item in sorted(root.glob(pattern))
            if item.is_file()
            and _is_path_within(item, workspace_root)
            and not _is_noise_path(item, root)
            and not is_gitignored(item, workspace_root, gitignore_rules)
            and not _is_sensitive_file_path(_display_path(item, workspace_root))
        ]
        paths = [_display_path(item, workspace_root) for item in files]
        truncated = len(paths) > limit
        visible_paths = paths[:limit]
        return ToolResult(
            tool_name=self.name,
            ok=True,
            summary=f"matched {len(paths)} files for {pattern}" + (" (truncated)" if truncated else ""),
            paths=visible_paths,
            data={"pattern": pattern, "path": _display_path(root, workspace_root), "total": len(paths), "limit": limit, "truncated": truncated},
        )

    def resource_keys(self, **kwargs: Any) -> list[str]:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path", ".")
        root = resolve_workspace_path(workspace, path)
        return [_display_path(root, workspace.expanduser().resolve())]


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a file inside the workspace."
    risk_level = "safe"
    is_read_only = True

    def describe_preview(self, **kwargs: Any) -> str:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path", "<missing>")
        return f"Read {path} in {workspace}"

    def preview(self, **kwargs) -> ToolPreview:
        return super().preview(**kwargs)

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path inside the workspace, for example README.md.",
                },
                "encoding": {"type": "string", "default": "utf-8"},
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes to read. When start_index is provided, this is the chunk length.",
                    "default": DEFAULT_MAX_READ_BYTES,
                    "minimum": 1,
                },
                "start_index": {
                    "type": "integer",
                    "description": "Optional zero-based byte offset. Use with max_bytes to read a slice of a large file.",
                    "default": 0,
                    "minimum": 0,
                },
                "start_line": {
                    "type": "integer",
                    "description": "Optional one-based line number. Use with limit_lines to read a code-safe line window.",
                    "minimum": 1,
                },
                "limit_lines": {
                    "type": "integer",
                    "description": "Maximum number of lines to return when start_line is provided.",
                    "default": DEFAULT_READ_LIMIT_LINES,
                    "minimum": 1,
                },
                "query": {
                    "type": "string",
                    "description": "Optional text to search before reading. Returns context windows around matching lines.",
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Lines of context before and after each query match.",
                    "default": DEFAULT_READ_CONTEXT_LINES,
                    "minimum": 0,
                },
                "max_matches": {
                    "type": "integer",
                    "description": "Maximum query matches to return.",
                    "default": DEFAULT_READ_MAX_MATCHES,
                    "minimum": 1,
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path")
        if not path:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="missing required argument: path",
                error_code="INVALID_TOOL_PARAMS",
            )
        try:
            target = _resolve_tool_path(self.name, workspace, path)
        except _ToolPathError as error:
            return error.result
        if not target.exists():
            return ToolResult(tool_name=self.name, ok=False, summary="file not found", error_code="FILE_NOT_FOUND")
        if not target.is_file():
            return ToolResult(tool_name=self.name, ok=False, summary="not a file", error_code="INVALID_TOOL_PARAMS")
        workspace_root = workspace.expanduser().resolve()
        relative_path = _display_path(target, workspace_root)
        if _is_sensitive_file_path(relative_path):
            return ToolResult(tool_name=self.name, ok=False, summary=f"protected read target: {relative_path}", error_code="PROTECTED_PATH")
        max_bytes = _positive_int(kwargs.get("max_bytes"), DEFAULT_MAX_READ_BYTES)
        size = target.stat().st_size
        query = str(kwargs.get("query", "")).strip()
        if query:
            return _read_file_query_window(
                target,
                path=str(path),
                query=query,
                encoding=str(kwargs.get("encoding", "utf-8")),
                context_lines=_non_negative_int(kwargs.get("context_lines"), DEFAULT_READ_CONTEXT_LINES),
                max_matches=_positive_int(kwargs.get("max_matches"), DEFAULT_READ_MAX_MATCHES),
            )
        if "start_line" in kwargs:
            return _read_file_line_window(
                target,
                path=str(path),
                start_line=_positive_int(kwargs.get("start_line"), 1),
                limit_lines=_positive_int(kwargs.get("limit_lines"), DEFAULT_READ_LIMIT_LINES),
                encoding=str(kwargs.get("encoding", "utf-8")),
            )
        has_start_index = "start_index" in kwargs
        if not has_start_index and _is_dependency_file_path(relative_path):
            result = _read_large_file_summary(
                target,
                path=str(path),
                max_bytes=max_bytes,
                encoding=str(kwargs.get("encoding", "utf-8")),
            )
            if result.ok:
                result.summary = f"dependency file summary for {path}"
                result.data["dependency_file_policy"] = True
                result.data["suggestion"] = "Use query or start_line with limit_lines for dependency files; avoid reading the whole file."
            return result
        if not has_start_index and _should_summarize_large_file_by_policy(relative_path, size):
            result = _read_large_file_summary(
                target,
                path=str(path),
                max_bytes=max_bytes,
                encoding=str(kwargs.get("encoding", "utf-8")),
            )
            if result.ok:
                result.data["large_file_policy"] = True
                result.data["suggestion"] = (
                    "Use query or start_line with limit_lines for generated/data files; avoid reading the whole file."
                )
            return result
        start_index = _non_negative_int(kwargs.get("start_index"), 0)
        if has_start_index and start_index > size:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"start_index {start_index} exceeds file size {size} bytes",
                error_code="INVALID_TOOL_PARAMS",
            )
        if has_start_index:
            with target.open("rb") as handle:
                handle.seek(start_index)
                raw = handle.read(max_bytes)
            if _looks_binary(raw):
                return ToolResult(
                    tool_name=self.name,
                    ok=False,
                    summary="binary file is not supported",
                    error_code="BINARY_FILE_UNSUPPORTED",
                )
            encoding = kwargs.get("encoding", "utf-8")
            try:
                content = raw.decode(encoding, errors="replace")
            except UnicodeDecodeError as error:
                return ToolResult(
                    tool_name=self.name,
                    ok=False,
                    summary=f"decode failed with {encoding}: {error}",
                    error_code="DECODE_ERROR",
                )
            return ToolResult(
                tool_name=self.name,
                ok=True,
                summary=f"read {path} from byte {start_index}",
                content=content,
                data={
                    "path": str(path),
                    "start_index": start_index,
                    "bytes_read": len(raw),
                    "file_size": size,
                    "truncated": start_index + len(raw) < size,
                    "decode_repaired": "\ufffd" in content,
                },
            )
        if size > max_bytes:
            return _read_large_file_summary(target, path=str(path), max_bytes=max_bytes, encoding=str(kwargs.get("encoding", "utf-8")))
        raw = target.read_bytes()
        if _looks_binary(raw):
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="binary file is not supported",
                error_code="BINARY_FILE_UNSUPPORTED",
            )
        encoding = kwargs.get("encoding", "utf-8")
        try:
            content = raw.decode(encoding)
        except UnicodeDecodeError as error:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"decode failed with {encoding}: {error}",
                error_code="DECODE_ERROR",
            )
        return ToolResult(
            tool_name=self.name,
            ok=True,
            summary=f"read {path}",
            content=content,
            data={"path": str(path), "file_size": size, "bytes_read": len(raw)},
        )

    def resource_keys(self, **kwargs: Any) -> list[str]:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path")
        if not path:
            return []
        try:
            target = _resolve_tool_path(self.name, workspace, path)
        except _ToolPathError:
            return []
        return [_display_path(target, workspace.expanduser().resolve())]


def _read_file_line_window(target: Path, *, path: str, start_line: int, limit_lines: int, encoding: str) -> ToolResult:
    selected_lines: list[str] = []
    total_lines = 0
    try:
        with target.open("r", encoding=encoding) as handle:
            for total_lines, line in enumerate(handle, start=1):
                if start_line <= total_lines < start_line + limit_lines:
                    selected_lines.append(line)
    except UnicodeDecodeError as error:
        return ToolResult(tool_name="read_file", ok=False, summary=f"decode failed with {encoding}: {error}", error_code="DECODE_ERROR")
    if start_line > total_lines:
        return ToolResult(
            tool_name="read_file",
            ok=False,
            summary=f"start_line {start_line} exceeds total lines {total_lines}",
            error_code="INVALID_TOOL_PARAMS",
            data={"start_line": start_line, "total_lines": total_lines},
        )
    start_index = start_line - 1
    end_line = min(total_lines, start_index + limit_lines)
    content = "".join(selected_lines)
    return ToolResult(
        tool_name="read_file",
        ok=True,
        summary=f"read {path} lines {start_line}-{end_line}",
        content=content,
        data={
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "truncated": start_line > 1 or end_line < total_lines,
        },
    )


def _read_file_query_window(target: Path, *, path: str, query: str, encoding: str, context_lines: int, max_matches: int) -> ToolResult:
    matcher, query_mode = _query_matcher(query)
    match_lines: list[int] = []
    captured_lines: dict[int, str] = {}
    previous_lines: deque[tuple[int, str]] = deque(maxlen=context_lines)
    capture_until_line = 0
    total_matches = 0
    total_lines = 0
    try:
        with target.open("r", encoding=encoding) as handle:
            for total_lines, line in enumerate(handle, start=1):
                if matcher(line):
                    total_matches += 1
                    if len(match_lines) < max_matches:
                        match_lines.append(total_lines)
                        for previous_line_number, previous_line in previous_lines:
                            captured_lines[previous_line_number] = previous_line
                        captured_lines[total_lines] = line
                        capture_until_line = max(capture_until_line, total_lines + context_lines)
                elif capture_until_line and total_lines <= capture_until_line:
                    captured_lines[total_lines] = line
                previous_lines.append((total_lines, line))
    except UnicodeDecodeError as error:
        return ToolResult(tool_name="read_file", ok=False, summary=f"decode failed with {encoding}: {error}", error_code="DECODE_ERROR")
    if not match_lines:
        return ToolResult(
            tool_name="read_file",
            ok=True,
            summary=f"no matches for query in {path}",
            data={"query": query, "query_mode": query_mode, "total_lines": total_lines, "negative_probe": True},
        )
    selected_matches = match_lines
    windows: list[tuple[int, int]] = []
    for line_number in selected_matches:
        start_line = max(1, line_number - context_lines)
        end_line = min(total_lines, line_number + context_lines)
        if windows and start_line <= windows[-1][1] + 1:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end_line))
        else:
            windows.append((start_line, end_line))

    chunks = []
    for start_line, end_line in windows:
        chunks.append(f"--- lines {start_line}-{end_line} ---\n")
        chunks.append("".join(captured_lines.get(line_number, "") for line_number in range(start_line, end_line + 1)))
        if chunks[-1] and not chunks[-1].endswith("\n"):
            chunks.append("\n")
    return ToolResult(
        tool_name="read_file",
        ok=True,
        summary=f"found {total_matches} match(es) for query in {path}",
        content="".join(chunks),
        data={
            "path": path,
            "query": query,
            "query_mode": query_mode,
            "matches": selected_matches,
            "total_matches": total_matches,
            "total_lines": total_lines,
            "truncated": total_matches > len(selected_matches),
            "windows": [{"start_line": start_line, "end_line": end_line} for start_line, end_line in windows],
        },
    )


def _query_matcher(query: str):
    if not _looks_like_regex_query(query):
        return lambda line: query in line, "literal"
    try:
        pattern = re.compile(query)
    except re.error:
        return lambda line: query in line, "literal"
    return lambda line: pattern.search(line) is not None, "regex"


def _looks_like_regex_query(query: str) -> bool:
    return any(char in query for char in ("|", ".*", "\\b", "^", "$", "[", "]", "(", ")", "+", "?"))


def _read_large_file_summary(target: Path, *, path: str, max_bytes: int, encoding: str) -> ToolResult:
    with target.open("rb") as handle:
        if _looks_binary(handle.read(min(max_bytes, 8192))):
            return ToolResult(
                tool_name="read_file",
                ok=False,
                summary="binary file is not supported",
                error_code="BINARY_FILE_UNSUPPORTED",
            )
    try:
        with target.open("r", encoding=encoding) as handle:
            preview_lines = [line for _, line in zip(range(DEFAULT_LARGE_FILE_PREVIEW_LINES), handle, strict=False)]
    except UnicodeDecodeError as error:
        return ToolResult(tool_name="read_file", ok=False, summary=f"decode failed with {encoding}: {error}", error_code="DECODE_ERROR")
    size = target.stat().st_size
    total_lines = _count_text_lines(target)
    return ToolResult(
        tool_name="read_file",
        ok=True,
        summary=f"file too large; returned summary for {path}",
        content=_truncate_large_file_preview("".join(preview_lines)),
        data={
            "path": path,
            "file_size": size,
            "max_bytes": max_bytes,
            "total_lines": total_lines,
            "preview_lines": len(preview_lines),
            "truncated": True,
            "suggestion": "Use query or start_line with limit_lines to read a targeted window.",
        },
    )


def _truncate_large_file_preview(content: str) -> str:
    if len(content) <= DEFAULT_LARGE_FILE_PREVIEW_CHARS:
        return content
    omitted = len(content) - DEFAULT_LARGE_FILE_PREVIEW_CHARS
    return content[:DEFAULT_LARGE_FILE_PREVIEW_CHARS].rstrip() + f"\n... [omitted {omitted} preview char(s)]\n"


def _should_summarize_large_file_by_policy(relative_path: str, size: int) -> bool:
    normalized = relative_path.replace("\\", "/")
    if size >= HARD_LARGE_FILE_SUMMARY_BYTES:
        return True
    generated_or_data_file = (
        normalized.startswith("data/")
        or "/data/" in normalized
        or ".generated." in normalized
        or normalized.endswith(".generated.js")
        or normalized.endswith(".generated.ts")
    )
    return generated_or_data_file and size >= GENERATED_DATA_FILE_SUMMARY_BYTES


def _is_dependency_file_path(relative_path: str) -> bool:
    return "node_modules/" in relative_path.replace("\\", "/")


def _count_text_lines(target: Path) -> int:
    with target.open("rb") as handle:
        total = sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1024 * 1024), b""))
    if target.stat().st_size == 0:
        return 0
    with target.open("rb") as handle:
        handle.seek(-1, os.SEEK_END)
        return total if handle.read(1) == b"\n" else total + 1


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _non_negative_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _is_noise_path(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        relative_parts = path.parts
    return any(part in NOISE_DIR_NAMES for part in relative_parts[:-1])


@dataclass(frozen=True)
class GitignoreRule:
    pattern: str
    negated: bool = False
    directory_only: bool = False
    anchored: bool = False


def load_gitignore_rules(workspace: Path) -> list[GitignoreRule]:
    gitignore = workspace / ".gitignore"
    if not gitignore.exists() or not gitignore.is_file():
        return []
    rules = []
    for raw_line in gitignore.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        if not line:
            continue
        anchored = line.startswith("/")
        if anchored:
            line = line[1:]
        directory_only = line.endswith("/")
        if directory_only:
            line = line.rstrip("/")
        if line:
            rules.append(
                GitignoreRule(
                    pattern=line,
                    negated=negated,
                    directory_only=directory_only,
                    anchored=anchored,
                )
            )
    return rules


def is_gitignored(path: Path, workspace: Path, rules: list[GitignoreRule]) -> bool:
    if not rules:
        return False
    try:
        relative = path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return True
    ignored = False
    for rule in rules:
        if _matches_gitignore_rule(relative, rule):
            ignored = not rule.negated
    return ignored


def _matches_gitignore_rule(relative: str, rule: GitignoreRule) -> bool:
    pattern = rule.pattern
    parts = relative.split("/")
    if rule.directory_only:
        if rule.anchored:
            return relative == pattern or relative.startswith(f"{pattern}/")
        return any(part == pattern for part in parts[:-1]) or relative.startswith(f"{pattern}/")

    candidates = [relative] if rule.anchored or "/" in pattern else [relative, parts[-1]]
    return any(fnmatch(candidate, pattern) for candidate in candidates)


def _looks_binary(content: bytes) -> bool:
    if not content:
        return False
    sample = content[:8192]
    return b"\x00" in sample


def _preview_existing_text(target: Path, limit: int = 160) -> str:
    if not target.exists() or not target.is_file():
        return ""
    try:
        content = target.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _display_path(path: Path, workspace_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(workspace_root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _is_path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class _ToolPathError(Exception):
    def __init__(self, result: ToolResult) -> None:
        super().__init__(result.summary)
        self.result = result


def _resolve_tool_path(tool_name: str, workspace: Path, path: str | Path) -> Path:
    try:
        return resolve_workspace_path(workspace, path)
    except PermissionError as error:
        remapped = _remap_leading_slash_project_path(workspace, path)
        if remapped is not None:
            return remapped
        code = str(error) or "PERMISSION_DENIED"
        summary = "path outside workspace" if code == "PATH_OUT_OF_WORKSPACE" else code
        raise _ToolPathError(ToolResult(tool_name=tool_name, ok=False, summary=summary, error_code=code)) from error


def _remap_leading_slash_project_path(workspace: Path, path: str | Path) -> Path | None:
    raw_path = str(path)
    if not raw_path.startswith("/") or raw_path.startswith("//"):
        return None
    stripped = raw_path.lstrip("/")
    if not stripped:
        return None
    first_part = stripped.split("/", 1)[0]
    if first_part not in {"src", "app", "tests", "test", "lib", "components", "pages", "packages", "docs"}:
        return None
    try:
        return resolve_workspace_path(workspace, stripped)
    except PermissionError:
        return None


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write a file inside the workspace."
    risk_level = "write"
    requires_confirmation = False
    is_read_only = False

    def describe_preview(self, **kwargs: Any) -> str:
        path = kwargs.get("path", "<missing>")
        return f"Write {path}"

    def preview(self, **kwargs) -> ToolPreview:
        return super().preview(**kwargs)

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative output file path inside the workspace."},
                "content": {"type": "string", "description": "File content to write."},
                "encoding": {"type": "string", "default": "utf-8"},
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes allowed for the written content.",
                    "default": DEFAULT_MAX_WRITE_BYTES,
                    "minimum": 1,
                },
                "allow_full_rewrite": {
                    "type": "boolean",
                    "description": "Set true only when replacing a large existing file is intentional; otherwise use replace_in_file.",
                    "default": False,
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path")
        content = kwargs.get("content")
        if not path:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="missing required argument: path",
                error_code="INVALID_TOOL_PARAMS",
            )
        if content is None:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="missing required argument: content",
                error_code="INVALID_TOOL_PARAMS",
            )
        try:
            target = _resolve_tool_path(self.name, workspace, path)
        except _ToolPathError as error:
            return error.result
        workspace_root = workspace.expanduser().resolve()
        relative_path = _display_path(target, workspace_root)
        previous_preview = _preview_existing_text(target)
        if _is_protected_write_path(Path(relative_path)):
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"protected write target: {relative_path}",
                error_code="PROTECTED_PATH",
            )
        if target.exists() and not target.is_file():
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"not a file: {relative_path}",
                error_code="INVALID_TOOL_PARAMS",
            )
        encoded = str(content).encode(kwargs.get("encoding", "utf-8"))
        max_bytes = _positive_int(kwargs.get("max_bytes"), DEFAULT_MAX_WRITE_BYTES)
        if len(encoded) > max_bytes:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"content too large: {len(encoded)} bytes exceeds {max_bytes} bytes",
                error_code="CONTENT_TOO_LARGE",
            )
        if (
            target.exists()
            and target.is_file()
            and target.stat().st_size >= FULL_REWRITE_WARNING_BYTES
            and not bool(kwargs.get("allow_full_rewrite", False))
        ):
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"existing file {relative_path} is large; use replace_in_file or pass allow_full_rewrite=true",
                error_code="FULL_REWRITE_REQUIRES_ALLOW",
                data={"file_size": target.stat().st_size, "threshold": FULL_REWRITE_WARNING_BYTES},
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
        return ToolResult(
            tool_name=self.name,
            ok=True,
            summary=f"wrote {path}",
            written_path=relative_path,
            data={"previous_content_preview": previous_preview},
        )

    def resource_keys(self, **kwargs: Any) -> list[str]:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path")
        if not path:
            return []
        try:
            target = _resolve_tool_path(self.name, workspace, path)
        except _ToolPathError:
            return []
        return [_display_path(target, workspace.expanduser().resolve())]


class ReplaceInFileTool(BaseTool):
    name = "replace_in_file"
    description = "Replace exact text inside an existing workspace file."
    risk_level = "write"
    requires_confirmation = False
    is_read_only = False

    def describe_preview(self, **kwargs: Any) -> str:
        path = kwargs.get("path", "<missing>")
        return f"Replace text in {path}"

    def preview(self, **kwargs) -> ToolPreview:
        return super().preview(**kwargs)

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path inside the workspace."},
                "old_text": {"type": "string", "description": "Exact existing text to replace."},
                "new_text": {"type": "string", "description": "Replacement text."},
                "before_text": {
                    "type": "string",
                    "description": "Optional exact text that must appear immediately before old_text.",
                },
                "after_text": {
                    "type": "string",
                    "description": "Optional exact text that must appear immediately after old_text.",
                },
                "expected_replacements": {
                    "type": "integer",
                    "description": "Exact number of occurrences expected. Defaults to 1 to avoid accidental broad edits.",
                    "default": 1,
                    "minimum": 1,
                },
                "encoding": {"type": "string", "default": "utf-8"},
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes allowed for the resulting file content.",
                    "default": DEFAULT_MAX_WRITE_BYTES,
                    "minimum": 1,
                },
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        }

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path")
        old_text = kwargs.get("old_text", kwargs.get("old_string"))
        new_text = kwargs.get("new_text", kwargs.get("new_string"))
        if not path:
            return ToolResult(tool_name=self.name, ok=False, summary="missing required argument: path", error_code="INVALID_TOOL_PARAMS")
        if old_text is None and new_text is None and ("start_line" in kwargs or "end_line" in kwargs):
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary="replace_in_file requires old_text/new_text; read the target line range first, then pass exact text",
                error_code="INVALID_TOOL_PARAMS",
            )
        if old_text is None:
            return ToolResult(tool_name=self.name, ok=False, summary="missing required argument: old_text", error_code="INVALID_TOOL_PARAMS")
        if new_text is None:
            return ToolResult(tool_name=self.name, ok=False, summary="missing required argument: new_text", error_code="INVALID_TOOL_PARAMS")
        try:
            target = _resolve_tool_path(self.name, workspace, path)
        except _ToolPathError as error:
            return error.result
        workspace_root = workspace.expanduser().resolve()
        relative_path = _display_path(target, workspace_root)
        if _is_protected_write_path(Path(relative_path)):
            return ToolResult(tool_name=self.name, ok=False, summary=f"protected write target: {relative_path}", error_code="PROTECTED_PATH")
        if not target.exists():
            return ToolResult(tool_name=self.name, ok=False, summary="file not found", error_code="FILE_NOT_FOUND")
        if not target.is_file():
            return ToolResult(tool_name=self.name, ok=False, summary="not a file", error_code="INVALID_TOOL_PARAMS")

        encoding = kwargs.get("encoding", "utf-8")
        try:
            content = target.read_text(encoding=encoding)
        except UnicodeDecodeError as error:
            return ToolResult(tool_name=self.name, ok=False, summary=f"decode failed with {encoding}: {error}", error_code="DECODE_ERROR")
        old_value = str(old_text)
        new_value = str(new_text)
        before_value = str(kwargs.get("before_text") or "")
        after_value = str(kwargs.get("after_text") or "")
        matches = _find_contextual_matches(content, old_value, before_text=before_value, after_text=after_value)
        old_text_occurrences = content.count(old_value)
        actual_replacements = len(matches)
        if old_text_occurrences == 0:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"old_text not found in {relative_path}",
                error_code="OLD_TEXT_NOT_FOUND",
                data={"actual_replacements": 0},
            )
        if actual_replacements == 0:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"context did not match old_text in {relative_path}",
                error_code="CONTEXT_MISMATCH",
                data={"old_text_occurrences": old_text_occurrences, "actual_replacements": 0},
            )
        expected_replacements = _positive_int(kwargs.get("expected_replacements"), 1)
        if actual_replacements != expected_replacements:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"expected {expected_replacements} replacement(s), found {actual_replacements}",
                error_code="REPLACEMENT_COUNT_MISMATCH",
                data={"expected_replacements": expected_replacements, "actual_replacements": actual_replacements},
            )

        updated = _replace_matches(content, matches, old_value, new_value)
        if updated == content:
            return ToolResult(
                tool_name=self.name,
                ok=True,
                summary=f"skipped {relative_path} (content unchanged)",
                written_path=relative_path,
                data={"replacements": actual_replacements, "write_strategy": "skipped"},
            )

        old_bytes = old_value.encode(encoding)
        new_bytes = new_value.encode(encoding)
        updated_bytes = updated.encode(encoding)
        max_bytes = _positive_int(kwargs.get("max_bytes"), DEFAULT_MAX_WRITE_BYTES)
        if len(updated_bytes) > max_bytes:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"content too large: {len(updated_bytes)} bytes exceeds {max_bytes} bytes",
                error_code="CONTENT_TOO_LARGE",
            )

        if len(old_bytes) == len(new_bytes) and actual_replacements == 1:
            offset = len(content[: matches[0]].encode(encoding))
            with target.open("r+b") as handle:
                handle.seek(offset)
                handle.write(new_bytes)
            strategy = "in_place"
        else:
            _atomic_write_bytes(target, updated_bytes)
            strategy = "atomic_replace"

        return ToolResult(
            tool_name=self.name,
            ok=True,
            summary=f"replaced {actual_replacements} occurrence{'s' if actual_replacements != 1 else ''} in {relative_path}",
            written_path=relative_path,
            data={"replacements": actual_replacements, "write_strategy": strategy},
        )

    def resource_keys(self, **kwargs: Any) -> list[str]:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path")
        if not path:
            return []
        try:
            target = _resolve_tool_path(self.name, workspace, path)
        except _ToolPathError:
            return []
        return [_display_path(target, workspace.expanduser().resolve())]


class AppendFileTool(BaseTool):
    name = "append_file"
    description = "Append text to a file inside the workspace."
    risk_level = "write"
    requires_confirmation = True
    is_read_only = False

    def describe_preview(self, **kwargs: Any) -> str:
        path = kwargs.get("path", "<missing>")
        return f"Append {path}"

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative output file path inside the workspace."},
                "content": {"type": "string", "description": "Text to append."},
                "encoding": {"type": "string", "default": "utf-8"},
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes allowed for the resulting content.",
                    "default": DEFAULT_MAX_WRITE_BYTES,
                    "minimum": 1,
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path")
        content = kwargs.get("content")
        if not path:
            return ToolResult(tool_name=self.name, ok=False, summary="missing required argument: path", error_code="INVALID_TOOL_PARAMS")
        if content is None:
            return ToolResult(tool_name=self.name, ok=False, summary="missing required argument: content", error_code="INVALID_TOOL_PARAMS")
        if not bool(kwargs.get("confirmed", False)):
            raise PermissionError("WRITE_REQUIRES_CONFIRMATION")

        try:
            target = _resolve_tool_path(self.name, workspace, path)
        except _ToolPathError as error:
            return error.result
        workspace_root = workspace.expanduser().resolve()
        relative_path = _display_path(target, workspace_root)
        previous_preview = _preview_existing_text(target)
        if _is_protected_write_path(Path(relative_path)):
            return ToolResult(tool_name=self.name, ok=False, summary=f"protected write target: {relative_path}", error_code="PROTECTED_PATH")
        if target.exists() and not target.is_file():
            return ToolResult(tool_name=self.name, ok=False, summary=f"not a file: {relative_path}", error_code="INVALID_TOOL_PARAMS")
        encoded = str(content).encode(kwargs.get("encoding", "utf-8"))
        max_bytes = _positive_int(kwargs.get("max_bytes"), DEFAULT_MAX_WRITE_BYTES)
        if target.exists():
            existing = target.read_bytes()
            if len(existing) + len(encoded) > max_bytes:
                return ToolResult(tool_name=self.name, ok=False, summary=f"content too large: {len(existing) + len(encoded)} bytes exceeds {max_bytes} bytes", error_code="CONTENT_TOO_LARGE")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab") as handle:
            handle.write(encoded)
        return ToolResult(
            tool_name=self.name,
            ok=True,
            summary=f"appended {path}",
            written_path=relative_path,
            data={"previous_content_preview": previous_preview},
        )


class MakeDirectoryTool(BaseTool):
    name = "make_directory"
    description = "Create a directory inside the workspace."
    risk_level = "safe"
    is_read_only = False

    def describe_preview(self, **kwargs: Any) -> str:
        path = kwargs.get("path", "<missing>")
        return f"Create directory {path}"

    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative directory path inside the workspace."}
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path")
        if not path:
            return ToolResult(tool_name=self.name, ok=False, summary="missing required argument: path", error_code="INVALID_TOOL_PARAMS")
        try:
            target = _resolve_tool_path(self.name, workspace, path)
        except _ToolPathError as error:
            return error.result
        workspace_root = workspace.expanduser().resolve()
        relative_path = _display_path(target, workspace_root)
        if _is_protected_write_path(Path(relative_path)):
            return ToolResult(tool_name=self.name, ok=False, summary=f"protected directory target: {relative_path}", error_code="PROTECTED_PATH")
        target.mkdir(parents=True, exist_ok=True)
        return ToolResult(tool_name=self.name, ok=True, summary=f"created directory {path}", written_path=relative_path)


def _is_protected_write_path(relative_path: Path) -> bool:
    parts = relative_path.parts
    if not parts:
        return True
    if _is_sensitive_file_path(relative_path):
        return True
    return any(part.startswith(".") for part in parts[:-1])


def _is_sensitive_file_path(path: str | Path) -> bool:
    candidate = Path(path)
    name = candidate.name
    if name.startswith(".env") and name not in SAFE_ENV_TEMPLATE_FILE_NAMES:
        return True
    return candidate.suffix.lower() in {".pem", ".key"}


def _atomic_write_bytes(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    finally:
        tmp_path.unlink(missing_ok=True)


def _find_contextual_matches(content: str, old_text: str, before_text: str = "", after_text: str = "") -> list[int]:
    if old_text == "":
        return []
    matches: list[int] = []
    start = 0
    while True:
        index = content.find(old_text, start)
        if index < 0:
            return matches
        before_ok = not before_text or content[:index].endswith(before_text)
        after_start = index + len(old_text)
        after_ok = not after_text or content[after_start:].startswith(after_text)
        if before_ok and after_ok:
            matches.append(index)
        start = index + len(old_text)


def _replace_matches(content: str, matches: list[int], old_text: str, new_text: str) -> str:
    if not matches:
        return content
    parts: list[str] = []
    cursor = 0
    for index in matches:
        parts.append(content[cursor:index])
        parts.append(new_text)
        cursor = index + len(old_text)
    parts.append(content[cursor:])
    return "".join(parts)
