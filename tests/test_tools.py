from pathlib import Path

import pytest

from manus_mini.tools import AppendFileTool, ListFilesTool, MakeDirectoryTool, ReadFileTool, ToolRegistry, WriteFileTool


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


def test_list_files_skips_noise_directories_and_applies_limit(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "mod.pyc").write_bytes(b"binary")
    for index in range(5):
        (tmp_path / f"file-{index}.txt").write_text(str(index), encoding="utf-8")

    result = ListFilesTool().run(workspace=tmp_path, limit=3)

    assert result.ok is True
    assert result.paths == ["file-0.txt", "file-1.txt", "file-2.txt"]
    assert result.data["truncated"] is True
    assert ".git/config" not in result.paths
    assert "__pycache__/mod.pyc" not in result.paths


def test_list_files_respects_workspace_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        "\n".join(
            [
                "outputs/",
                "runs/",
                "*.log",
                ".env.*",
                "!.env.example",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "old.md").write_text("old", encoding="utf-8")
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "events.jsonl").write_text("{}", encoding="utf-8")
    (tmp_path / "debug.log").write_text("debug", encoding="utf-8")
    (tmp_path / ".env.local").write_text("secret", encoding="utf-8")
    (tmp_path / ".env.example").write_text("example", encoding="utf-8")
    (tmp_path / "README.md").write_text("readme", encoding="utf-8")

    result = ListFilesTool().run(workspace=tmp_path)

    assert result.ok is True
    assert "README.md" in result.paths
    assert ".env.example" in result.paths
    assert "outputs/old.md" not in result.paths
    assert "runs/events.jsonl" not in result.paths
    assert "debug.log" not in result.paths
    assert ".env.local" not in result.paths


def test_list_files_skips_common_build_and_dependency_outputs_without_gitignore(tmp_path: Path) -> None:
    ignored_files = {
        "node_modules/pkg/index.js",
        "dist/app.js",
        "build/lib.py",
        "target/classes/App.class",
        ".gradle/cache.bin",
        ".mvn/wrapper/maven-wrapper.jar",
        "vendor/module/file.go",
        "bin/server",
        "obj/app.o",
        "pkg/mod/cache.zip",
        ".tox/py/lib.py",
        ".nox/session/log.txt",
        "coverage/lcov.info",
        ".next/server/page.js",
        ".nuxt/app.js",
        ".turbo/cache.bin",
        ".cache/tool/cache.bin",
        ".idea/workspace.xml",
        ".vscode/settings.json",
    }
    for path in ignored_files:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ignored", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')", encoding="utf-8")

    result = ListFilesTool().run(workspace=tmp_path, limit=200)

    assert result.ok is True
    assert result.paths == ["src/main.py"]


def test_read_file_rejects_binary_files(tmp_path: Path) -> None:
    (tmp_path / "image.bin").write_bytes(b"\x00\x01\x02\x03")

    result = ReadFileTool().run(workspace=tmp_path, path="image.bin")

    assert result.ok is False
    assert result.error_code == "BINARY_FILE_UNSUPPORTED"


def test_read_file_rejects_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("x" * 20, encoding="utf-8")

    result = ReadFileTool().run(workspace=tmp_path, path="big.txt", max_bytes=10)

    assert result.ok is False
    assert result.error_code == "FILE_TOO_LARGE"


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


def test_write_file_rejects_sensitive_or_hidden_targets(tmp_path: Path) -> None:
    tool = WriteFileTool()

    env_result = tool.run(workspace=tmp_path, path=".env", content="LLM_API_KEY=x", confirmed=True)
    hidden_dir_result = tool.run(workspace=tmp_path, path=".secret/value.txt", content="x", confirmed=True)

    assert env_result.ok is False
    assert env_result.error_code == "PROTECTED_PATH"
    assert hidden_dir_result.ok is False
    assert hidden_dir_result.error_code == "PROTECTED_PATH"
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / ".secret" / "value.txt").exists()


def test_write_file_rejects_oversized_content(tmp_path: Path) -> None:
    result = WriteFileTool().run(
        workspace=tmp_path,
        path="large.txt",
        content="x" * 20,
        max_bytes=10,
        confirmed=True,
    )

    assert result.ok is False
    assert result.error_code == "CONTENT_TOO_LARGE"
    assert not (tmp_path / "large.txt").exists()


def test_file_tools_return_invalid_params_for_missing_required_args(tmp_path: Path) -> None:
    read_result = ReadFileTool().run(workspace=tmp_path)
    write_path_result = WriteFileTool().run(workspace=tmp_path, content="hello")
    write_content_result = WriteFileTool().run(workspace=tmp_path, path="draft.txt")

    assert read_result.ok is False
    assert read_result.error_code == "INVALID_TOOL_PARAMS"
    assert "path" in read_result.summary

    assert write_path_result.ok is False
    assert write_path_result.error_code == "INVALID_TOOL_PARAMS"
    assert "path" in write_path_result.summary

    assert write_content_result.ok is False
    assert write_content_result.error_code == "INVALID_TOOL_PARAMS"
    assert "content" in write_content_result.summary


def test_tool_registry_exposes_default_file_tools() -> None:
    registry = ToolRegistry()

    assert isinstance(registry.get("list_files"), ListFilesTool)
    assert isinstance(registry.get("read_file"), ReadFileTool)
    assert isinstance(registry.get("write_file"), WriteFileTool)
    assert isinstance(registry.get("append_file"), AppendFileTool)
    assert isinstance(registry.get("make_directory"), MakeDirectoryTool)


def test_append_file_appends_content_with_confirmation(tmp_path: Path) -> None:
    tool = AppendFileTool()
    (tmp_path / "draft.txt").write_text("hello", encoding="utf-8")

    with pytest.raises(PermissionError):
        tool.run(workspace=tmp_path, path="draft.txt", content=" world")

    result = tool.run(workspace=tmp_path, path="draft.txt", content=" world", confirmed=True)

    assert result.ok is True
    assert (tmp_path / "draft.txt").read_text(encoding="utf-8") == "hello world"


def test_make_directory_creates_nested_directory(tmp_path: Path) -> None:
    result = MakeDirectoryTool().run(workspace=tmp_path, path="a/b/c")

    assert result.ok is True
    assert (tmp_path / "a" / "b" / "c").is_dir()
