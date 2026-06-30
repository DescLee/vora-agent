from __future__ import annotations

from manus_mini.tools.file_tools import ListFilesTool, ReadFileTool, WriteFileTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools = {
            "list_files": ListFilesTool(),
            "read_file": ReadFileTool(),
            "write_file": WriteFileTool(),
        }

    def get(self, name: str):
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)
