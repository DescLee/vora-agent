from __future__ import annotations

from pathlib import Path


def collect_local_docs(workspace: str | Path, patterns: tuple[str, ...] = ("README.md", "docs/*.md")) -> list[Path]:
    root = Path(workspace)
    results: list[Path] = []
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                results.append(path)
    return sorted(set(results))


def summarize_text(text: str, max_lines: int = 5) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "暂无内容。"
    return "\n".join(lines[:max_lines])


def generate_markdown_report(title: str, sections: dict[str, str], sources: list[str] | None = None) -> str:
    parts = [f"# {title}", ""]
    if sources:
        parts.extend(["## Sources", *[f"- {source}" for source in sources], ""])
    for heading, content in sections.items():
        parts.extend([f"## {heading}", "", content.strip() or "暂无内容。", ""])
    return "\n".join(parts).strip() + "\n"
