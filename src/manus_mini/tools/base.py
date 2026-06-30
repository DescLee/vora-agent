from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class ToolPreview(BaseModel):
    tool_name: str = ""
    summary: str
    risk_level: Literal["safe", "write", "command"] = "safe"
    requires_confirmation: bool = False
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    tool_name: str = ""
    ok: bool
    summary: str
    content: str = ""
    paths: list[str] = Field(default_factory=list)
    written_path: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None


@runtime_checkable
class ToolProtocol(Protocol):
    name: str
    risk_level: Literal["safe", "write", "command"]
    requires_confirmation: bool
    is_read_only: bool

    def preview(self, **kwargs: Any) -> ToolPreview:
        ...

    def run(self, **kwargs: Any) -> ToolResult:
        ...

    def resource_keys(self, **kwargs: Any) -> list[str]:
        ...


Tool = ToolProtocol


class BaseTool(ABC):
    name: str
    risk_level: Literal["safe", "write", "command"] = "safe"
    requires_confirmation: bool = False
    is_read_only: bool = True

    def preview(self, **kwargs: Any) -> ToolPreview:
        return ToolPreview(
            tool_name=self.name,
            summary=self.describe_preview(**kwargs),
            risk_level=self.risk_level,
            requires_confirmation=self.requires_confirmation,
            args=dict(kwargs),
        )

    @abstractmethod
    def run(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError

    def resource_keys(self, **kwargs: Any) -> list[str]:
        return []

    def describe_preview(self, **kwargs: Any) -> str:
        return self.name


def resolve_workspace_path(workspace: Path, path: str | Path) -> Path:
    workspace_root = workspace.expanduser().resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.expanduser().resolve(strict=False)
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise PermissionError("PATH_OUT_OF_WORKSPACE") from exc
    return resolved
