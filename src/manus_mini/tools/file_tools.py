from __future__ import annotations

from pathlib import Path
from typing import Any

from manus_mini.tools.base import BaseTool, ToolPreview, ToolResult, resolve_workspace_path


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
        root = resolve_workspace_path(workspace, path)
        if not root.exists():
            return ToolResult(tool_name=self.name, ok=False, summary="workspace not found", error_code="FILE_NOT_FOUND")

        if root.is_file():
            files = [root]
        else:
            files = [item for item in sorted(root.rglob("*")) if item.is_file()]

        workspace_root = workspace.expanduser().resolve()
        paths = [item.resolve().relative_to(workspace_root).as_posix() for item in files]
        return ToolResult(tool_name=self.name, ok=True, summary=f"found {len(paths)} files", paths=paths)

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
        path = kwargs["path"]
        return f"Read {path} in {workspace}"

    def preview(self, **kwargs) -> ToolPreview:
        return super().preview(**kwargs)

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs["path"]
        target = resolve_workspace_path(workspace, path)
        if not target.exists():
            return ToolResult(tool_name=self.name, ok=False, summary="file not found", error_code="FILE_NOT_FOUND")
        if not target.is_file():
            return ToolResult(tool_name=self.name, ok=False, summary="not a file", error_code="INVALID_TOOL_PARAMS")
        return ToolResult(
            tool_name=self.name,
            ok=True,
            summary=f"read {path}",
            content=target.read_text(encoding=kwargs.get("encoding", "utf-8")),
        )

    def resource_keys(self, **kwargs: Any) -> list[str]:
        workspace = Path(kwargs["workspace"])
        path = kwargs["path"]
        target = resolve_workspace_path(workspace, path)
        return [target.resolve().relative_to(workspace.expanduser().resolve()).as_posix()]


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write a file inside the workspace."
    risk_level = "write"
    requires_confirmation = True
    is_read_only = False

    def describe_preview(self, **kwargs: Any) -> str:
        path = kwargs["path"]
        return f"Write {path}"

    def preview(self, **kwargs) -> ToolPreview:
        return super().preview(**kwargs)

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs["path"]
        content = kwargs["content"]
        confirmed = bool(kwargs.get("confirmed", False))
        if not confirmed:
            raise PermissionError("WRITE_REQUIRES_CONFIRMATION")

        target = resolve_workspace_path(workspace, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding=kwargs.get("encoding", "utf-8"))
        relative_path = target.resolve().relative_to(workspace.expanduser().resolve()).as_posix()
        return ToolResult(
            tool_name=self.name,
            ok=True,
            summary=f"wrote {path}",
            written_path=relative_path,
        )

    def resource_keys(self, **kwargs: Any) -> list[str]:
        workspace = Path(kwargs["workspace"])
        path = kwargs["path"]
        target = resolve_workspace_path(workspace, path)
        return [target.resolve().relative_to(workspace.expanduser().resolve()).as_posix()]
