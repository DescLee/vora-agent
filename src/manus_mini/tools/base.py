from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ToolPreview(BaseModel):
    summary: str
    requires_confirmation: bool = False
    risk_level: str = "safe"


class ToolResult(BaseModel):
    ok: bool
    summary: str
    content: str = ""
    paths: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None


class Tool(Protocol):
    name: str
    risk_level: str

    def preview(self, **kwargs: Any) -> ToolPreview:
        ...

    def run(self, **kwargs: Any) -> ToolResult:
        ...


def resolve_workspace_path(workspace: Path, path: str) -> Path:
    root = workspace.resolve()
    target = (root / path).resolve() if not Path(path).is_absolute() else Path(path).resolve()
    if root not in target.parents and target != root:
        raise PermissionError("PATH_OUT_OF_WORKSPACE")
    return target
