import os
from pathlib import Path

import pytest

from vora.project_index import build_cached_project_index


def test_project_index_builds_lightweight_repo_map_without_source_symbols(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"vite build"},"dependencies":{"react":"18.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(
        "export function startApp() { return true }\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "app.test.ts").write_text("test('app', () => {})\n", encoding="utf-8")

    index = build_cached_project_index(tmp_path)

    assert "项目轻量地图" in index
    assert "项目类型：node" in index
    assert "Manifest：package.json" in index
    assert "顶层目录：src/, tests/" in index
    assert "入口候选：src/main.ts" in index
    assert "startApp" not in index
    assert "app.test.ts" not in index


def test_project_index_does_not_rebuild_for_deep_source_content_change(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "app.py"
    source.write_text("def old_symbol():\n    return True\n", encoding="utf-8")

    first = build_cached_project_index(tmp_path)
    source.write_text("def new_symbol():\n    return True\n", encoding="utf-8")
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    second = build_cached_project_index(tmp_path)

    assert first == second
    assert "old_symbol" not in first
    assert "new_symbol" not in second


def test_project_index_skips_symlinks_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-index-source.py"
    outside.write_text("def external_secret_symbol(): pass\n", encoding="utf-8")
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlink unavailable: {error}")

    index = build_cached_project_index(tmp_path)

    assert "linked.py" not in index
    assert "external_secret_symbol" not in index
