from pathlib import Path

import pytest

from vora.tools.base import ToolResult, resolve_workspace_path
from vora.react import format_tool_result_message
from vora.tools import (
    AppendFileTool,
    FetchWebpageTool,
    GlobTool,
    ListFilesTool,
    MakeDirectoryTool,
    ReadFileTool,
    ReplaceInFileTool,
    ToolRegistry,
    WebSearchTool,
    WriteFileTool,
)


def test_read_file_rejects_escape_from_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    result = ReadFileTool().run(workspace=tmp_path, path="../outside.txt")

    assert result.ok is False
    assert result.error_code == "PATH_OUT_OF_WORKSPACE"
    assert "workspace" in result.summary


def test_list_files_treats_leading_slash_project_path_as_workspace_relative(tmp_path: Path) -> None:
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "src" / "api" / "http.ts").write_text("export const ok = true\n", encoding="utf-8")

    result = ListFilesTool().run(workspace=tmp_path, path="/src/api")

    assert result.ok is True
    assert result.paths == ["src/api/http.ts"]


def test_read_file_treats_leading_slash_project_path_as_workspace_relative(tmp_path: Path) -> None:
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "src" / "api" / "http.ts").write_text("export const ok = true\n", encoding="utf-8")

    result = ReadFileTool().run(workspace=tmp_path, path="/src/api/http.ts")

    assert result.ok is True
    assert "export const ok" in result.content
    assert result.data["path"] == "/src/api/http.ts"


def test_format_tool_result_message_truncates_content_in_the_middle() -> None:
    result = ToolResult(
        tool_name="run_bash",
        ok=True,
        summary="command exited 0",
        content="HEAD-" + ("x" * 5000) + "-TAIL",
    )

    message = format_tool_result_message(result)

    assert "HEAD-" in message
    assert "-TAIL" in message
    assert "omitted" in message


def test_file_write_tools_return_structured_error_for_workspace_escape(tmp_path: Path) -> None:
    results = [
        WriteFileTool().run(workspace=tmp_path, path="../outside.txt", content="x", confirmed=True),
        ReplaceInFileTool().run(workspace=tmp_path, path="../outside.txt", old_text="x", new_text="y"),
        AppendFileTool().run(workspace=tmp_path, path="../outside.txt", content="x", confirmed=True),
        MakeDirectoryTool().run(workspace=tmp_path, path="../outside-dir"),
    ]

    assert [result.error_code for result in results] == ["PATH_OUT_OF_WORKSPACE"] * 4
    assert all(result.ok is False for result in results)


def test_glob_tool_finds_matching_workspace_files(tmp_path: Path) -> None:
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "src" / "api" / "http.ts").write_text("", encoding="utf-8")
    (tmp_path / "src" / "api" / "user.ts").write_text("", encoding="utf-8")
    (tmp_path / "src" / "view.txt").write_text("", encoding="utf-8")

    result = GlobTool().run(workspace=tmp_path, pattern="src/**/*.ts")

    assert result.ok is True
    assert result.paths == ["src/api/http.ts", "src/api/user.ts"]


def test_file_write_tools_reject_directory_targets_with_structured_error(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()

    results = [
        WriteFileTool().run(workspace=tmp_path, path="notes", content="x", confirmed=True),
        AppendFileTool().run(workspace=tmp_path, path="notes", content="x", confirmed=True),
    ]

    assert [result.error_code for result in results] == ["INVALID_TOOL_PARAMS", "INVALID_TOOL_PARAMS"]
    assert all(result.ok is False for result in results)
    assert all("not a file" in result.summary for result in results)


def test_resolve_workspace_path_allows_system_tmp(tmp_path: Path) -> None:
    target = resolve_workspace_path(tmp_path, "/tmp/vora-test.txt")

    assert target.resolve().is_relative_to(Path("/tmp").resolve(strict=False))


def test_write_file_allows_system_tmp_path(tmp_path: Path) -> None:
    target = Path("/tmp") / "vora-write-test.txt"
    if target.exists():
        target.unlink()

    result = WriteFileTool().run(
        workspace=tmp_path,
        path=str(target),
        content="hello",
        confirmed=True,
    )

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "hello"
    assert result.written_path is not None
    assert result.written_path.endswith("vora-write-test.txt")
    target.unlink()


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


def test_list_files_skips_sensitive_files_without_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")
    (tmp_path / ".env.test").write_text("LLM_API_KEY=test", encoding="utf-8")
    (tmp_path / ".env.example").write_text("LLM_API_KEY=", encoding="utf-8")
    (tmp_path / "private.pem").write_text("pem", encoding="utf-8")
    (tmp_path / "service.key").write_text("key", encoding="utf-8")
    (tmp_path / "README.md").write_text("readme", encoding="utf-8")

    result = ListFilesTool().run(workspace=tmp_path)

    assert result.ok is True
    assert result.paths == [".env.example", "README.md"]


def test_list_files_skips_symlink_to_file_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-visible.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "inside.txt").write_text("inside", encoding="utf-8")
    (tmp_path / "outside-link.txt").symlink_to(outside)

    result = ListFilesTool().run(workspace=tmp_path)

    assert result.ok is True
    assert result.paths == ["inside.txt"]


def test_list_files_skips_symlink_to_directory_outside_workspace(tmp_path: Path) -> None:
    outside_dir = tmp_path.parent / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("outside", encoding="utf-8")
    (tmp_path / "inside.txt").write_text("inside", encoding="utf-8")
    (tmp_path / "outside-dir-link").symlink_to(outside_dir, target_is_directory=True)

    result = ListFilesTool().run(workspace=tmp_path)

    assert result.ok is True
    assert result.paths == ["inside.txt"]


def test_read_file_rejects_binary_files(tmp_path: Path) -> None:
    (tmp_path / "image.bin").write_bytes(b"\x00\x01\x02\x03")

    result = ReadFileTool().run(workspace=tmp_path, path="image.bin")

    assert result.ok is False
    assert result.error_code == "BINARY_FILE_UNSUPPORTED"


def test_read_file_rejects_sensitive_files(tmp_path: Path) -> None:
    (tmp_path / ".env.test").write_text("LLM_API_KEY=test", encoding="utf-8")
    (tmp_path / ".env.example").write_text("LLM_API_KEY=", encoding="utf-8")
    (tmp_path / "private.pem").write_text("pem", encoding="utf-8")

    env_result = ReadFileTool().run(workspace=tmp_path, path=".env.test")
    pem_result = ReadFileTool().run(workspace=tmp_path, path="private.pem")
    example_result = ReadFileTool().run(workspace=tmp_path, path=".env.example")

    assert env_result.ok is False
    assert env_result.error_code == "PROTECTED_PATH"
    assert pem_result.ok is False
    assert pem_result.error_code == "PROTECTED_PATH"
    assert example_result.ok is True
    assert example_result.content == "LLM_API_KEY="


def test_read_file_returns_summary_for_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("line 1\nline 2\nline 3\nline 4\n", encoding="utf-8")

    result = ReadFileTool().run(workspace=tmp_path, path="big.txt", max_bytes=10)

    assert result.ok is True
    assert result.summary == "file too large; returned summary for big.txt"
    assert result.data["file_size"] > 10
    assert result.data["total_lines"] == 4
    assert result.data["truncated"] is True
    assert result.data["suggestion"] == "Use query or start_line with limit_lines to read a targeted window."
    assert "line 1" in result.content
    assert "line 4" not in result.content


def test_read_file_summarizes_large_files_by_default(tmp_path: Path) -> None:
    content = "module.exports=" + ("x" * 250_000)
    target = tmp_path / "data.js"
    target.write_text(content, encoding="utf-8")

    result = ReadFileTool().run(workspace=tmp_path, path="data.js")

    assert result.ok is True
    assert result.summary == "file too large; returned summary for data.js"
    assert len(result.content) < 2_000
    assert result.data["truncated"] is True
    assert result.data["file_size"] == target.stat().st_size


def test_read_file_summarizes_generated_data_files_even_with_large_max_bytes(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    target = data_dir / "questions.js"
    target.write_text("module.exports=[" + ("x" * 180_000) + "]", encoding="utf-8")

    result = ReadFileTool().run(workspace=tmp_path, path="data/questions.js", max_bytes=1_000_000)

    assert result.ok is True
    assert result.summary == "file too large; returned summary for data/questions.js"
    assert result.data["large_file_policy"] is True
    assert result.data["truncated"] is True
    assert len(result.content) < 2_000


def test_read_file_reads_line_window(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("\n".join(f"line {index}" for index in range(1, 8)) + "\n", encoding="utf-8")

    result = ReadFileTool().run(workspace=tmp_path, path="module.py", start_line=3, limit_lines=3)

    assert result.ok is True
    assert result.summary == "read module.py lines 3-5"
    assert result.content == "line 3\nline 4\nline 5\n"
    assert result.data["start_line"] == 3
    assert result.data["end_line"] == 5
    assert result.data["total_lines"] == 7
    assert result.data["truncated"] is True


def test_read_file_searches_query_and_returns_context_windows(tmp_path: Path) -> None:
    content = "\n".join(
        [
            "def alpha():",
            "    pass",
            "",
            "def target_handler():",
            "    return 'first'",
            "",
            "def beta():",
            "    pass",
            "",
            "def target_helper():",
            "    return 'second'",
        ]
    )
    (tmp_path / "module.py").write_text(content + "\n", encoding="utf-8")

    result = ReadFileTool().run(workspace=tmp_path, path="module.py", query="target_", context_lines=1)

    assert result.ok is True
    assert result.summary == "found 2 match(es) for query in module.py"
    assert "def target_handler" in result.content
    assert "def target_helper" in result.content
    assert result.data["query"] == "target_"
    assert result.data["matches"] == [4, 10]
    assert result.data["windows"] == [
        {"start_line": 3, "end_line": 5},
        {"start_line": 9, "end_line": 11},
    ]


def test_read_file_query_supports_regex_alternatives(tmp_path: Path) -> None:
    (tmp_path / "module.ts").write_text(
        "import { request } from './request'\n"
        "const value = 1\n"
        "export const getData = () => request('/data')\n",
        encoding="utf-8",
    )

    result = ReadFileTool().run(workspace=tmp_path, path="module.ts", query="import|export|getData", context_lines=0)

    assert result.ok is True
    assert result.summary == "found 2 match(es) for query in module.ts"
    assert "import { request }" in result.content
    assert "export const getData" in result.content
    assert result.data["query_mode"] == "regex"
    assert result.data["matches"] == [1, 3]


def test_read_file_query_reports_no_matches(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("def alpha():\n    pass\n", encoding="utf-8")

    result = ReadFileTool().run(workspace=tmp_path, path="module.py", query="missing_symbol")

    assert result.ok is True
    assert result.error_code is None
    assert result.summary == "no matches for query in module.py"
    assert result.data["query"] == "missing_symbol"
    assert result.data["negative_probe"] is True
    assert result.data["total_lines"] == 2


def test_read_file_reads_slice_from_start_index(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("0123456789abcdef", encoding="utf-8")

    result = ReadFileTool().run(workspace=tmp_path, path="big.txt", start_index=5, max_bytes=4)

    assert result.ok is True
    assert result.content == "5678"
    assert result.summary == "read big.txt from byte 5"
    assert result.data["start_index"] == 5
    assert result.data["bytes_read"] == 4
    assert result.data["file_size"] == 16
    assert result.data["truncated"] is True


def test_read_file_slice_tolerates_utf8_boundary_offset(tmp_path: Path) -> None:
    (tmp_path / "unicode.txt").write_text("前缀\n工具调度\n后续", encoding="utf-8")
    raw = (tmp_path / "unicode.txt").read_bytes()
    start_index = raw.index("工具".encode("utf-8")) + 1

    result = ReadFileTool().run(workspace=tmp_path, path="unicode.txt", start_index=start_index, max_bytes=12)

    assert result.ok is True
    assert result.error_code is None
    assert "具" in result.content
    assert result.data["start_index"] == start_index


def test_read_file_rejects_start_index_beyond_file_size(tmp_path: Path) -> None:
    (tmp_path / "small.txt").write_text("abc", encoding="utf-8")

    result = ReadFileTool().run(workspace=tmp_path, path="small.txt", start_index=4)

    assert result.ok is False
    assert result.error_code == "INVALID_TOOL_PARAMS"
    assert "start_index" in result.summary


def test_write_file_executes_without_confirmation(tmp_path: Path) -> None:
    tool = WriteFileTool()

    preview = tool.preview(workspace=tmp_path, path="draft.txt", content="hello")

    assert preview.requires_confirmation is False
    assert preview.risk_level == "write"

    result = tool.run(
        workspace=tmp_path,
        path="draft.txt",
        content="hello",
    )

    assert result.ok is True
    assert (tmp_path / "draft.txt").read_text(encoding="utf-8") == "hello"


def test_write_file_rejects_sensitive_or_hidden_targets(tmp_path: Path) -> None:
    tool = WriteFileTool()

    env_result = tool.run(workspace=tmp_path, path=".env", content="LLM_API_KEY=x", confirmed=True)
    env_test_result = tool.run(workspace=tmp_path, path=".env.test", content="LLM_API_KEY=x", confirmed=True)
    env_example_result = tool.run(workspace=tmp_path, path=".env.example", content="LLM_API_KEY=", confirmed=True)
    hidden_dir_result = tool.run(workspace=tmp_path, path=".secret/value.txt", content="x", confirmed=True)

    assert env_result.ok is False
    assert env_result.error_code == "PROTECTED_PATH"
    assert env_test_result.ok is False
    assert env_test_result.error_code == "PROTECTED_PATH"
    assert env_example_result.ok is True
    assert hidden_dir_result.ok is False
    assert hidden_dir_result.error_code == "PROTECTED_PATH"
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / ".env.test").exists()
    assert (tmp_path / ".env.example").read_text(encoding="utf-8") == "LLM_API_KEY="
    assert not (tmp_path / ".secret" / "value.txt").exists()


def test_append_file_rejects_env_variant_targets(tmp_path: Path) -> None:
    result = AppendFileTool().run(workspace=tmp_path, path=".env.test", content="LLM_API_KEY=x", confirmed=True)

    assert result.ok is False
    assert result.error_code == "PROTECTED_PATH"
    assert not (tmp_path / ".env.test").exists()


def test_replace_in_file_rejects_env_variant_targets(tmp_path: Path) -> None:
    target = tmp_path / ".env.test"
    target.write_text("LLM_API_KEY=old\n", encoding="utf-8")

    result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path=".env.test",
        old_text="old",
        new_text="new",
        confirmed=True,
    )

    assert result.ok is False
    assert result.error_code == "PROTECTED_PATH"
    assert target.read_text(encoding="utf-8") == "LLM_API_KEY=old\n"


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


def test_write_file_rejects_large_existing_file_rewrite_without_explicit_allow(tmp_path: Path) -> None:
    target = tmp_path / "large.py"
    target.write_text("x" * 5000, encoding="utf-8")

    result = WriteFileTool().run(
        workspace=tmp_path,
        path="large.py",
        content="y" * 5000,
        confirmed=True,
    )

    assert result.ok is False
    assert result.error_code == "FULL_REWRITE_REQUIRES_ALLOW"
    assert "replace_in_file" in result.summary
    assert target.read_text(encoding="utf-8") == "x" * 5000


def test_write_file_allows_large_existing_file_rewrite_when_explicitly_allowed(tmp_path: Path) -> None:
    target = tmp_path / "large.py"
    target.write_text("x" * 5000, encoding="utf-8")

    result = WriteFileTool().run(
        workspace=tmp_path,
        path="large.py",
        content="y" * 5000,
        confirmed=True,
        allow_full_rewrite=True,
    )

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "y" * 5000


def test_replace_in_file_replaces_unique_text_without_confirmation(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("def hello():\n    return 'old'\n", encoding="utf-8")
    tool = ReplaceInFileTool()

    result = tool.run(
        workspace=tmp_path,
        path="app.py",
        old_text="'old'",
        new_text="'new'",
    )

    assert result.ok is True
    assert result.summary == "replaced 1 occurrence in app.py"
    assert target.read_text(encoding="utf-8") == "def hello():\n    return 'new'\n"
    assert result.data["replacements"] == 1


def test_replace_in_file_accepts_old_string_new_string_aliases(tmp_path: Path) -> None:
    target = tmp_path / "app.txt"
    target.write_text("hello old\n", encoding="utf-8")

    result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path="app.txt",
        old_string="old",
        new_string="new",
    )

    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "hello new\n"


def test_replace_in_file_rejects_line_range_without_text(tmp_path: Path) -> None:
    target = tmp_path / "app.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path="app.txt",
        start_line=1,
        end_line=1,
    )

    assert result.ok is False
    assert result.error_code == "INVALID_TOOL_PARAMS"
    assert "old_text/new_text" in result.summary


def test_replace_in_file_rejects_missing_old_text(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")

    result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path="app.py",
        old_text="missing",
        new_text="found",
    )

    assert result.ok is False
    assert result.error_code == "OLD_TEXT_NOT_FOUND"


def test_replace_in_file_rejects_unexpected_replacement_count(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("value = 1\nvalue = 1\n", encoding="utf-8")

    result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path="app.py",
        old_text="value = 1",
        new_text="value = 2",
    )

    assert result.ok is False
    assert result.error_code == "REPLACEMENT_COUNT_MISMATCH"
    assert result.data["actual_replacements"] == 2


def test_replace_in_file_allows_expected_multiple_replacements(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\nvalue = 1\n", encoding="utf-8")

    result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path="app.py",
        old_text="value = 1",
        new_text="value = 2",
        expected_replacements=2,
    )

    assert result.ok is True
    assert result.data["replacements"] == 2
    assert target.read_text(encoding="utf-8") == "value = 2\nvalue = 2\n"


def test_replace_in_file_uses_before_and_after_context_to_pick_match(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("first = value\nsecond = value\n", encoding="utf-8")

    result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path="app.py",
        before_text="second = ",
        old_text="value",
        after_text="\n",
        new_text="updated",
    )

    assert result.ok is True
    assert result.data["replacements"] == 1
    assert target.read_text(encoding="utf-8") == "first = value\nsecond = updated\n"


def test_replace_in_file_rejects_when_context_does_not_match(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("first = value\nsecond = value\n", encoding="utf-8")

    result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path="app.py",
        before_text="missing = ",
        old_text="value",
        new_text="updated",
    )

    assert result.ok is False
    assert result.error_code == "CONTEXT_MISMATCH"
    assert result.data["old_text_occurrences"] == 2


def test_replace_in_file_uses_in_place_strategy_for_equal_length_change(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("status = 'old'\n", encoding="utf-8")

    result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path="app.py",
        old_text="'old'",
        new_text="'new'",
    )

    assert result.ok is True
    assert result.data["write_strategy"] == "in_place"
    assert target.read_text(encoding="utf-8") == "status = 'new'\n"


def test_replace_in_file_uses_atomic_replace_for_length_change(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("status = 'old'\n", encoding="utf-8")

    result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path="app.py",
        old_text="'old'",
        new_text="'new value'",
    )

    assert result.ok is True
    assert result.data["write_strategy"] == "atomic_replace"
    assert target.read_text(encoding="utf-8") == "status = 'new value'\n"


def test_replace_in_file_skips_when_result_is_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("status = 'old'\n", encoding="utf-8")
    before_mtime = target.stat().st_mtime_ns

    result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path="app.py",
        old_text="'old'",
        new_text="'old'",
    )

    assert result.ok is True
    assert result.summary == "skipped app.py (content unchanged)"
    assert result.data["write_strategy"] == "skipped"
    assert target.stat().st_mtime_ns == before_mtime


def test_file_tools_return_invalid_params_for_missing_required_args(tmp_path: Path) -> None:
    read_result = ReadFileTool().run(workspace=tmp_path)
    write_path_result = WriteFileTool().run(workspace=tmp_path, content="hello")
    write_content_result = WriteFileTool().run(workspace=tmp_path, path="draft.txt")
    replace_old_text_result = ReplaceInFileTool().run(
        workspace=tmp_path,
        path="draft.txt",
        new_text="new",
    )

    assert read_result.ok is False
    assert read_result.error_code == "INVALID_TOOL_PARAMS"
    assert "path" in read_result.summary

    assert write_path_result.ok is False
    assert write_path_result.error_code == "INVALID_TOOL_PARAMS"
    assert "path" in write_path_result.summary

    assert write_content_result.ok is False
    assert write_content_result.error_code == "INVALID_TOOL_PARAMS"
    assert "content" in write_content_result.summary

    assert replace_old_text_result.ok is False
    assert replace_old_text_result.error_code == "INVALID_TOOL_PARAMS"
    assert "old_text" in replace_old_text_result.summary


def test_tool_registry_exposes_default_file_tools() -> None:
    registry = ToolRegistry()

    assert isinstance(registry.get("list_files"), ListFilesTool)
    assert isinstance(registry.get("glob"), GlobTool)
    assert isinstance(registry.get("read_file"), ReadFileTool)
    assert isinstance(registry.get("write_file"), WriteFileTool)
    assert isinstance(registry.get("replace_in_file"), ReplaceInFileTool)
    assert registry.get("write_file").requires_confirmation is False
    assert registry.get("replace_in_file").requires_confirmation is False
    assert isinstance(registry.get("append_file"), AppendFileTool)
    assert isinstance(registry.get("make_directory"), MakeDirectoryTool)
    assert isinstance(registry.get("web_search"), WebSearchTool)
    assert isinstance(registry.get("fetch_webpage"), FetchWebpageTool)


def test_web_search_validates_query() -> None:
    result = WebSearchTool().run(query="")

    assert result.ok is False
    assert result.error_code == "INVALID_TOOL_PARAMS"
    assert "query" in result.summary


def test_web_search_rejects_invalid_max_results() -> None:
    result = WebSearchTool().run(query="manus", max_results="many")

    assert result.ok is False
    assert result.error_code == "INVALID_TOOL_PARAMS"
    assert "max_results" in result.summary


def test_web_search_formats_results(monkeypatch) -> None:
    from vora.tools import search_tools

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def text(self, query, max_results):  # noqa: ANN001, ANN201
            assert query == "manus"
            assert max_results == 2
            return [
                {"title": "Manus", "body": "Agent product", "href": "https://example.com/manus"},
            ]

    monkeypatch.setattr(search_tools, "DDGS", FakeDDGS)

    result = WebSearchTool().run(query="manus", max_results=2)

    assert result.ok is True
    assert result.summary == "Found 1 results for: manus"
    assert "1. Manus" in result.content
    assert "Agent product" in result.content
    assert "URL: https://example.com/manus" in result.content
    assert result.data["result_count"] == 1


def test_web_search_redacts_secret_values_in_result_urls(monkeypatch) -> None:
    from vora.tools import search_tools

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def text(self, query, max_results):  # noqa: ANN001, ANN201, ARG002
            return [
                {
                    "title": "Callback",
                    "body": "OAuth callback",
                    "href": "https://example.com/callback?access_token=plain-secret&ok=1",
                },
            ]

    monkeypatch.setattr(search_tools, "DDGS", FakeDDGS)

    result = WebSearchTool().run(query="callback")

    assert result.ok is True
    assert "plain-secret" not in result.content
    assert "access_token=[REDACTED]" in result.content


def test_web_search_redacts_secret_values_in_query_outputs(monkeypatch) -> None:
    from vora.tools import search_tools

    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def text(self, query, max_results):  # noqa: ANN001, ANN201, ARG002
            return []

    monkeypatch.setattr(search_tools, "DDGS", FakeDDGS)

    result = WebSearchTool().run(query="status access_token=plain-secret")

    assert result.ok is True
    assert "plain-secret" not in result.summary
    assert "plain-secret" not in result.data["query"]
    assert "access_token=[REDACTED]" in result.summary


def test_web_search_suppresses_duckduckgo_package_rename_warning(monkeypatch, recwarn, capsys) -> None:
    from vora.tools import search_tools
    import sys
    import warnings

    class WarningDDGS:
        def __init__(self) -> None:
            print("failed to load native root certificate", file=sys.stderr)
            warnings.warn(
                "This package (`duckduckgo_search`) has been renamed to `ddgs`!",
                RuntimeWarning,
                stacklevel=2,
            )

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def text(self, query, max_results):  # noqa: ANN001, ANN201, ARG002
            return []

    monkeypatch.setattr(search_tools, "DDGS", WarningDDGS)

    result = WebSearchTool().run(query="manus")

    assert result.ok is True
    assert len(recwarn) == 0
    assert capsys.readouterr().err == ""
    assert result.data["warnings"] == [
        "RuntimeWarning: This package (`duckduckgo_search`) has been renamed to `ddgs`!"
    ]
    assert result.data["stderr"] == ["failed to load native root certificate"]


def test_fetch_webpage_validates_url() -> None:
    result = FetchWebpageTool().run(url="ftp://example.com")

    assert result.ok is False
    assert result.error_code == "INVALID_TOOL_PARAMS"
    assert "http" in result.summary


def test_fetch_webpage_accepts_uppercase_http_scheme(monkeypatch) -> None:
    from vora.tools import search_tools
    import socket

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = "<html><body>Hello</body></html>"
        url = "HTTP://example.com"
        is_redirect = False

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, timeout, headers, allow_redirects):  # noqa: ANN001, ANN201
        assert url == "HTTP://example.com"
        assert allow_redirects is False
        return FakeResponse()

    def fake_getaddrinfo(host, port, type=0):  # noqa: ANN001, ANN202, ARG002
        return [(search_tools.socket.AF_INET, search_tools.socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))]

    monkeypatch.setattr(search_tools, "socket", socket, raising=False)
    monkeypatch.setattr(search_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(search_tools.requests, "get", fake_get)

    result = FetchWebpageTool().run(url="HTTP://example.com")

    assert result.ok is True
    assert "Hello" in result.content


def test_fetch_webpage_rejects_invalid_max_chars() -> None:
    result = FetchWebpageTool().run(url="https://example.com", max_chars="lots")

    assert result.ok is False
    assert result.error_code == "INVALID_TOOL_PARAMS"
    assert "max_chars" in result.summary


def test_fetch_webpage_rejects_invalid_port_without_crashing() -> None:
    result = FetchWebpageTool().run(url="https://example.com:notaport")

    assert result.ok is False
    assert result.error_code == "INVALID_TOOL_PARAMS"
    assert "port" in result.summary


def test_fetch_webpage_preview_redacts_secret_values_in_url() -> None:
    preview = FetchWebpageTool().preview(url="https://example.com/callback?access_token=plain-secret&ok=1")

    assert "plain-secret" not in preview.summary
    assert "plain-secret" not in preview.args["url"]
    assert "access_token=[REDACTED]" in preview.summary
    assert preview.args["url"] == "https://example.com/callback?access_token=[REDACTED]&ok=1"


def test_fetch_webpage_rejects_private_network_literal_urls() -> None:
    for url in (
        "http://127.0.0.1:8000/admin",
        "http://localhost:8000/admin",
        "http://169.254.169.254/latest/meta-data/",
    ):
        result = FetchWebpageTool().run(url=url)

        assert result.ok is False
        assert result.error_code == "PROTECTED_URL"
        assert "protected URL" in result.summary


def test_fetch_webpage_rejects_hosts_that_resolve_to_private_addresses(monkeypatch) -> None:
    from vora.tools import search_tools
    import socket

    def fake_getaddrinfo(host, port, type=0):  # noqa: ANN001, ANN202
        assert host == "internal.example"
        assert port == 443
        assert type == search_tools.socket.SOCK_STREAM
        return [(search_tools.socket.AF_INET, search_tools.socket.SOCK_STREAM, 0, "", ("10.0.0.5", port))]

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = "<html><body>internal secret</body></html>"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(search_tools, "socket", socket, raising=False)
    monkeypatch.setattr(search_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(search_tools.requests, "get", lambda *args, **kwargs: FakeResponse())

    result = FetchWebpageTool().run(url="https://internal.example/secret")

    assert result.ok is False
    assert result.error_code == "PROTECTED_URL"
    assert "protected URL" in result.summary


def test_fetch_webpage_strips_html(monkeypatch) -> None:
    from vora.tools import search_tools
    import socket

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = "<html><head><title>x</title></head><body><h1>Hello</h1><script>bad()</script><p>A&amp;B</p></body></html>"

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, timeout, headers, allow_redirects):  # noqa: ANN001, ANN201
        assert url == "https://example.com"
        assert timeout == 15
        assert "User-Agent" in headers
        assert allow_redirects is False
        return FakeResponse()

    def fake_getaddrinfo(host, port, type=0):  # noqa: ANN001, ANN202
        return [(search_tools.socket.AF_INET, search_tools.socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))]

    monkeypatch.setattr(search_tools, "socket", socket, raising=False)
    monkeypatch.setattr(search_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(search_tools.requests, "get", fake_get)

    result = FetchWebpageTool().run(url="https://example.com", max_chars=1000)

    assert result.ok is True
    assert "Hello" in result.content
    assert "A&B" in result.content
    assert "bad()" not in result.content
    assert result.data["content_type"] == "text/html"


def test_fetch_webpage_decodes_numeric_html_entities(monkeypatch) -> None:
    from vora.tools import search_tools
    import socket

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = "<html><body><p>&#20320;&#22909; &copy;</p></body></html>"
        url = "https://example.com"
        is_redirect = False

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, timeout, headers, allow_redirects):  # noqa: ANN001, ANN201, ARG002
        return FakeResponse()

    def fake_getaddrinfo(host, port, type=0):  # noqa: ANN001, ANN202, ARG002
        return [(search_tools.socket.AF_INET, search_tools.socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))]

    monkeypatch.setattr(search_tools, "socket", socket, raising=False)
    monkeypatch.setattr(search_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(search_tools.requests, "get", fake_get)

    result = FetchWebpageTool().run(url="https://example.com", max_chars=1000)

    assert result.ok is True
    assert "你好 ©" in result.content


def test_fetch_webpage_rejects_redirect_to_private_network(monkeypatch) -> None:
    from vora.tools import search_tools
    import socket

    class RedirectResponse:
        headers = {"location": "http://127.0.0.1/admin"}
        text = ""
        url = "https://example.com"
        is_redirect = True

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, timeout, headers, allow_redirects):  # noqa: ANN001, ANN201, ARG002
        assert allow_redirects is False
        return RedirectResponse()

    def fake_getaddrinfo(host, port, type=0):  # noqa: ANN001, ANN202, ARG002
        if host == "example.com":
            return [(search_tools.socket.AF_INET, search_tools.socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))]
        if host == "127.0.0.1":
            return [(search_tools.socket.AF_INET, search_tools.socket.SOCK_STREAM, 0, "", ("127.0.0.1", port))]
        raise AssertionError(host)

    monkeypatch.setattr(search_tools, "socket", socket, raising=False)
    monkeypatch.setattr(search_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(search_tools.requests, "get", fake_get)

    result = FetchWebpageTool().run(url="https://example.com")

    assert result.ok is False
    assert result.error_code == "PROTECTED_URL"
    assert "redirect" in result.summary


def test_fetch_webpage_redacts_secret_values_in_url_outputs(monkeypatch) -> None:
    from vora.tools import search_tools
    import socket

    class FakeResponse:
        headers = {"content-type": "text/html"}
        text = "<html><body>Hello</body></html>"

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, timeout, headers, allow_redirects):  # noqa: ANN001, ANN201, ARG002
        assert allow_redirects is False
        return FakeResponse()

    def fake_getaddrinfo(host, port, type=0):  # noqa: ANN001, ANN202, ARG002
        return [(search_tools.socket.AF_INET, search_tools.socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))]

    monkeypatch.setattr(search_tools, "socket", socket, raising=False)
    monkeypatch.setattr(search_tools.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(search_tools.requests, "get", fake_get)

    result = FetchWebpageTool().run(url="https://example.com/callback?access_token=plain-secret&ok=1")

    assert result.ok is True
    assert "plain-secret" not in result.summary
    assert "plain-secret" not in result.data["url"]
    assert "access_token=[REDACTED]" in result.summary
    assert result.data["url"] == "https://example.com/callback?access_token=[REDACTED]&ok=1"


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


def test_make_directory_rejects_hidden_directories(tmp_path: Path) -> None:
    result = MakeDirectoryTool().run(workspace=tmp_path, path=".secret/cache")

    assert result.ok is False
    assert result.error_code == "PROTECTED_PATH"
    assert not (tmp_path / ".secret").exists()
