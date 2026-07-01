from __future__ import annotations

from difflib import unified_diff
from pathlib import Path


def scan_project(workspace: str | Path) -> list[str]:
    root = Path(workspace)
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def read_code_file(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def propose_patch(original: str, revised: str, filename: str = "file.txt") -> str:
    return "".join(
        unified_diff(
            original.splitlines(keepends=True),
            revised.splitlines(keepends=True),
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
    )


def apply_text_edit(text: str, old: str, new: str) -> str:
    return text.replace(old, new)
