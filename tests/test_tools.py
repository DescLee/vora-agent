from pathlib import Path

import pytest

from manus_mini.tools import ListFilesTool, ReadFileTool, ToolRegistry, WriteFileTool


def test_read_file_rejects_escape_from_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    tool = ReadFileTool()

    with pytest.raises(PermissionError):
        tool.run(workspace=tmp_path, path="../outside.txt")


def test_list_files_returns_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("guide", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("notes", encoding="utf-8")

    tool = ListFilesTool()
    result = tool.run(workspace=tmp_path)

    assert result.ok is True
    assert result.paths == ["docs/guide.md", "notes.txt"]


def test_write_file_preview_requires_confirmation(tmp_path: Path) -> None:
    tool = WriteFileTool()

    preview = tool.preview(workspace=tmp_path, path="draft.txt", content="hello")

    assert preview.requires_confirmation is True
    assert preview.risk_level == "write"

    with pytest.raises(PermissionError):
        tool.run(workspace=tmp_path, path="draft.txt", content="hello")

    result = tool.run(
        workspace=tmp_path,
        path="draft.txt",
        content="hello",
        confirmed=True,
    )

    assert result.ok is True
    assert (tmp_path / "draft.txt").read_text(encoding="utf-8") == "hello"


def test_tool_registry_exposes_default_file_tools() -> None:
    registry = ToolRegistry()

    assert isinstance(registry.get("list_files"), ListFilesTool)
    assert isinstance(registry.get("read_file"), ReadFileTool)
    assert isinstance(registry.get("write_file"), WriteFileTool)
