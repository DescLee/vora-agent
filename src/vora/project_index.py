from __future__ import annotations

import json
from pathlib import Path

from vora.logging import project_cache_dir
from vora.tools.file_tools import NOISE_DIR_NAMES, _is_path_within, _is_sensitive_file_path


PROJECT_INDEX_CACHE_VERSION = 2
PROJECT_INDEX_CACHE_FILENAME = "project-index.json"

MANIFEST_NAMES = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Makefile",
)
ROOT_ENTRY_NAMES = (
    "app.py",
    "main.py",
    "manage.py",
    "cli.py",
    "server.py",
    "main.go",
    "main.rs",
    "main.ts",
    "main.tsx",
    "main.js",
    "main.jsx",
    "index.ts",
    "index.tsx",
    "index.js",
    "index.jsx",
)
SOURCE_ENTRY_NAMES = (
    "main.py",
    "app.py",
    "cli.py",
    "server.py",
    "main.go",
    "main.rs",
    "main.ts",
    "main.tsx",
    "main.js",
    "main.jsx",
    "index.ts",
    "index.tsx",
    "index.js",
    "index.jsx",
)


def build_cached_project_index(workspace: Path) -> str:
    root = workspace.expanduser().resolve()
    if not root.is_dir():
        return "项目轻量地图\n- 当前工作目录不存在或不可访问。"
    repo_map = _build_repo_map(root)
    signature = json.dumps(repo_map, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cache_path = project_cache_dir(root) / PROJECT_INDEX_CACHE_FILENAME
    cached = _read_cache(cache_path, signature)
    if cached is not None:
        return cached
    rendered = _render_repo_map(repo_map)
    _write_cache(cache_path, signature, repo_map, rendered)
    return rendered


def _build_repo_map(root: Path) -> dict:
    manifests = [name for name in MANIFEST_NAMES if _safe_file(root / name, root)]
    top_level_directories = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        children = []
    for child in children:
        if not child.is_dir() or child.is_symlink():
            continue
        if child.name in NOISE_DIR_NAMES or child.name.startswith("."):
            continue
        top_level_directories.append(f"{child.name}/")

    entries = []
    for name in ROOT_ENTRY_NAMES:
        candidate = root / name
        if _safe_file(candidate, root):
            entries.append(name)
    for directory in ("src", "app"):
        for name in SOURCE_ENTRY_NAMES:
            relative = f"{directory}/{name}"
            if _safe_file(root / relative, root):
                entries.append(relative)
    cmd_root = root / "cmd"
    if cmd_root.is_dir() and not cmd_root.is_symlink():
        try:
            cmd_children = sorted(cmd_root.iterdir(), key=lambda path: path.name.lower())
        except OSError:
            cmd_children = []
        for child in cmd_children[:12]:
            candidate = child / "main.go"
            if child.is_dir() and _safe_file(candidate, root):
                entries.append(candidate.relative_to(root).as_posix())

    return {
        "project_type": _detect_project_type(set(manifests)),
        "manifests": manifests,
        "top_level_directories": top_level_directories[:20],
        "entries": entries[:12],
    }


def _safe_file(path: Path, root: Path) -> bool:
    if not path.is_file() or path.is_symlink() or not _is_path_within(path, root):
        return False
    return not _is_sensitive_file_path(path.relative_to(root).as_posix())


def _detect_project_type(manifests: set[str]) -> str:
    if "package.json" in manifests:
        return "node"
    if manifests & {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"}:
        return "python"
    if "go.mod" in manifests:
        return "go"
    if "Cargo.toml" in manifests:
        return "rust"
    if manifests & {"pom.xml", "build.gradle", "build.gradle.kts"}:
        return "java"
    return "generic"


def _render_repo_map(repo_map: dict) -> str:
    return "\n".join(
        [
            "项目轻量地图",
            "- 该地图只包含浅层结构，不读取源码正文；请使用 search_code/glob 按问题定位证据。",
            f"- 项目类型：{repo_map['project_type']}",
            f"- Manifest：{_format_values(repo_map['manifests'])}",
            f"- 顶层目录：{_format_values(repo_map['top_level_directories'])}",
            f"- 入口候选：{_format_values(repo_map['entries'])}",
        ]
    )


def _format_values(values: list[str]) -> str:
    return ", ".join(values) if values else "[empty]"


def _read_cache(path: Path, signature: str) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("version") != PROJECT_INDEX_CACHE_VERSION or payload.get("signature") != signature:
        return None
    rendered = payload.get("rendered")
    return rendered if isinstance(rendered, str) and rendered.strip() else None


def _write_cache(path: Path, signature: str, repo_map: dict, rendered: str) -> None:
    payload = {
        "version": PROJECT_INDEX_CACHE_VERSION,
        "signature": signature,
        "repo_map": repo_map,
        "rendered": rendered,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return


__all__ = ["build_cached_project_index"]
