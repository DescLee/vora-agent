from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from manus_mini.tools.base import BaseTool, ToolPreview, ToolResult, resolve_workspace_path


DEFAULT_LIST_LIMIT = 500
DEFAULT_MAX_READ_BYTES = 1_000_000
DEFAULT_MAX_WRITE_BYTES = 1_000_000
NOISE_DIR_NAMES = {
    ".cache",
    ".dart_tool",
    ".git",
    ".gradle",
    ".hg",
    ".idea",
    ".manus",
    ".manus-mini",
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
PROTECTED_FILE_NAMES = {".env", ".env.local", ".env.production", ".env.development"}


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

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path", ".")
        limit = _positive_int(kwargs.get("limit"), DEFAULT_LIST_LIMIT)
        root = resolve_workspace_path(workspace, path)
        if not root.exists():
            return ToolResult(tool_name=self.name, ok=False, summary="workspace not found", error_code="FILE_NOT_FOUND")

        workspace_root = workspace.expanduser().resolve()
        gitignore_rules = load_gitignore_rules(workspace_root)

        if root.is_file():
            files = [root] if not is_gitignored(root, workspace_root, gitignore_rules) else []
        else:
            files = [
                item
                for item in sorted(root.rglob("*"))
                if item.is_file()
                and not _is_noise_path(item, root)
                and not is_gitignored(item, workspace_root, gitignore_rules)
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
        target = resolve_workspace_path(workspace, path)
        if not target.exists():
            return ToolResult(tool_name=self.name, ok=False, summary="file not found", error_code="FILE_NOT_FOUND")
        if not target.is_file():
            return ToolResult(tool_name=self.name, ok=False, summary="not a file", error_code="INVALID_TOOL_PARAMS")
        max_bytes = _positive_int(kwargs.get("max_bytes"), DEFAULT_MAX_READ_BYTES)
        size = target.stat().st_size
        has_start_index = "start_index" in kwargs
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
                summary=f"read {path} from byte {start_index}",
                content=content,
                data={
                    "start_index": start_index,
                    "bytes_read": len(raw),
                    "file_size": size,
                    "truncated": start_index + len(raw) < size,
                },
            )
        if size > max_bytes:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"file too large: {size} bytes exceeds {max_bytes} bytes",
                error_code="FILE_TOO_LARGE",
            )
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
        )

    def resource_keys(self, **kwargs: Any) -> list[str]:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path")
        if not path:
            return []
        target = resolve_workspace_path(workspace, path)
        return [_display_path(target, workspace.expanduser().resolve())]


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
    relative = path.resolve().relative_to(workspace.resolve()).as_posix()
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


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write a file inside the workspace."
    risk_level = "write"
    requires_confirmation = True
    is_read_only = False

    def describe_preview(self, **kwargs: Any) -> str:
        path = kwargs.get("path", "<missing>")
        return f"Write {path}"

    def preview(self, **kwargs) -> ToolPreview:
        return super().preview(**kwargs)

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
        confirmed = bool(kwargs.get("confirmed", False))
        if not confirmed:
            raise PermissionError("WRITE_REQUIRES_CONFIRMATION")

        target = resolve_workspace_path(workspace, path)
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
        encoded = str(content).encode(kwargs.get("encoding", "utf-8"))
        max_bytes = _positive_int(kwargs.get("max_bytes"), DEFAULT_MAX_WRITE_BYTES)
        if len(encoded) > max_bytes:
            return ToolResult(
                tool_name=self.name,
                ok=False,
                summary=f"content too large: {len(encoded)} bytes exceeds {max_bytes} bytes",
                error_code="CONTENT_TOO_LARGE",
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
        target = resolve_workspace_path(workspace, path)
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

        target = resolve_workspace_path(workspace, path)
        workspace_root = workspace.expanduser().resolve()
        relative_path = _display_path(target, workspace_root)
        previous_preview = _preview_existing_text(target)
        if _is_protected_write_path(Path(relative_path)):
            return ToolResult(tool_name=self.name, ok=False, summary=f"protected write target: {relative_path}", error_code="PROTECTED_PATH")
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

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs.get("path")
        if not path:
            return ToolResult(tool_name=self.name, ok=False, summary="missing required argument: path", error_code="INVALID_TOOL_PARAMS")
        target = resolve_workspace_path(workspace, path)
        target.mkdir(parents=True, exist_ok=True)
        workspace_root = workspace.expanduser().resolve()
        relative_path = _display_path(target, workspace_root)
        return ToolResult(tool_name=self.name, ok=True, summary=f"created directory {path}", written_path=relative_path)


def _is_protected_write_path(relative_path: Path) -> bool:
    parts = relative_path.parts
    if not parts:
        return True
    if relative_path.name in PROTECTED_FILE_NAMES:
        return True
    return any(part.startswith(".") for part in parts[:-1])
