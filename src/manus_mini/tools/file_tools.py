from __future__ import annotations

from pathlib import Path

from manus_mini.tools.base import ToolPreview, ToolResult, resolve_workspace_path


class ListFilesTool:
    name = "list_files"
    risk_level = "safe"

    def preview(self, **kwargs) -> ToolPreview:
        workspace = Path(kwargs["workspace"])
        return ToolPreview(summary=f"List files in {workspace}", risk_level=self.risk_level)

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        root = workspace.resolve()
        if not root.exists():
            return ToolResult(ok=False, summary="workspace not found", error_code="FILE_NOT_FOUND")

        paths = [
            str(path.relative_to(root)).replace("\\", "/")
            for path in sorted(root.rglob("*"))
            if path.is_file()
        ]
        return ToolResult(ok=True, summary=f"found {len(paths)} files", paths=paths)


class ReadFileTool:
    name = "read_file"
    risk_level = "safe"

    def preview(self, **kwargs) -> ToolPreview:
        workspace = Path(kwargs["workspace"])
        path = kwargs["path"]
        return ToolPreview(summary=f"Read {path} in {workspace}", risk_level=self.risk_level)

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs["path"]
        target = resolve_workspace_path(workspace, path)
        if not target.exists():
            return ToolResult(ok=False, summary="file not found", error_code="FILE_NOT_FOUND")
        return ToolResult(ok=True, summary=f"read {path}", content=target.read_text(encoding="utf-8"))


class WriteFileTool:
    name = "write_file"
    risk_level = "write"

    def preview(self, **kwargs) -> ToolPreview:
        path = kwargs["path"]
        return ToolPreview(
            summary=f"Write {path}",
            requires_confirmation=True,
            risk_level=self.risk_level,
        )

    def run(self, **kwargs) -> ToolResult:
        workspace = Path(kwargs["workspace"])
        path = kwargs["path"]
        content = kwargs["content"]
        confirmed = bool(kwargs.get("confirmed", False))
        if not confirmed:
            raise PermissionError("WRITE_REQUIRES_CONFIRMATION")

        target = resolve_workspace_path(workspace, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        return ToolResult(ok=True, summary=f"wrote {path}")
