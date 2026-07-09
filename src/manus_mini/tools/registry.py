from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from manus_mini.tools.base import ToolProtocol
from manus_mini.tools.file_tools import AppendFileTool, ListFilesTool, MakeDirectoryTool, ReadFileTool, ReplaceInFileTool, WriteFileTool
from manus_mini.tools.search_tools import FetchWebpageTool, WebSearchTool
from manus_mini.tools.shell_tools import RunBashTool, RunTempScriptTool


class ToolRegistry:
    def __init__(self, tools: Iterable[ToolProtocol] | None = None) -> None:
        self._tools: dict[str, ToolProtocol] = {}
        default_tools = tools if tools is not None else (
            ListFilesTool(),
            ReadFileTool(),
            WriteFileTool(),
            ReplaceInFileTool(),
            AppendFileTool(),
            MakeDirectoryTool(),
            WebSearchTool(),
            FetchWebpageTool(),
            RunBashTool(),
            RunTempScriptTool(),
        )
        for tool in default_tools:
            self.register(cast(ToolProtocol, tool))

    def register(self, tool: ToolProtocol) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolProtocol:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def all(self) -> list[ToolProtocol]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
