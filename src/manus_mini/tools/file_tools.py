from __future__ import annotations

from pathlib import Path
from typing import Any

from manus_mini.tools.base import BaseTool, ToolPreview, ToolResult, resolve_workspace_path


DEFAULT_LIST_LIMIT = 500
DEFAULT_MAX_READ_BYTES = 1_000_000
DEFAULT_MAX_WRITE_BYTES = 1_000_000
NOISE_DIR_NAMES = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".venv", "venv"}
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

        if root.is_file():
            files = [root]
        else:
            files = [item for item in sorted(root.rglob("*")) if item.is_file() and not _is_noise_path(item, root)]

        workspace_root = workspace.expanduser().resolve()
        paths = [item.resolve().relative_to(workspace_root).as_posix() for item in files]
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
        return [root.resolve().relative_to(workspace.expanduser().resolve()).as_posix()]


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
        return [target.resolve().relative_to(workspace.expanduser().resolve()).as_posix()]


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _is_noise_path(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        relative_parts = path.parts
    return any(part in NOISE_DIR_NAMES for part in relative_parts[:-1])


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
        relative_path = target.resolve().relative_to(workspace.expanduser().resolve()).as_posix()
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
        return [target.resolve().relative_to(workspace.expanduser().resolve()).as_posix()]


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
        relative_path = target.resolve().relative_to(workspace.expanduser().resolve()).as_posix()
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
        relative_path = target.resolve().relative_to(workspace.expanduser().resolve()).as_posix()
        return ToolResult(tool_name=self.name, ok=True, summary=f"created directory {path}", written_path=relative_path)


def _is_protected_write_path(relative_path: Path) -> bool:
    parts = relative_path.parts
    if not parts:
        return True
    if relative_path.name in PROTECTED_FILE_NAMES:
        return True
    return any(part.startswith(".") for part in parts[:-1])
