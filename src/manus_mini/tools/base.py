from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from manus_mini.redaction import redact_sensitive_text, redact_sensitive_value


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

    def parameters_schema(self) -> dict[str, Any]:
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
            summary=redact_sensitive_text(self.describe_preview(**kwargs)),
            risk_level=self.risk_level,
            requires_confirmation=self.requires_confirmation,
            args=redact_sensitive_value(dict(kwargs)),
        )

    @abstractmethod
    def run(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError

    def resource_keys(self, **kwargs: Any) -> list[str]:
        return []

    def describe_preview(self, **kwargs: Any) -> str:
        return self.name

    def parameters_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": True}


def resolve_workspace_path(workspace: Path, path: str | Path) -> Path:
    workspace_root = workspace.expanduser().resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.expanduser().resolve(strict=False)
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        # 系统临时目录允许作为受控例外，避免工具无法读写 /tmp 下的短期文件。
        if _is_system_tmp_path(resolved):
            return resolved
        raise PermissionError("PATH_OUT_OF_WORKSPACE") from exc
    return resolved


def _is_system_tmp_path(path: Path) -> bool:
    try:
        return path.is_relative_to(Path("/tmp").expanduser().resolve(strict=False))
    except AttributeError:
        try:
            path.relative_to(Path("/tmp").expanduser().resolve(strict=False))
            return True
        except ValueError:
            return False
    except ValueError:
        return False
