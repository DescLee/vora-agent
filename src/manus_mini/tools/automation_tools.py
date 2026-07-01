from __future__ import annotations


def extract_todos(text: str) -> list[str]:
    todos: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "TODO" in stripped or stripped.startswith("- ") or stripped.startswith("* "):
            todos.append(stripped)
    return todos


def organize_notes(notes: list[str]) -> str:
    if not notes:
        return "暂无笔记。"
    return "\n".join(f"- {note}" for note in notes)


def generate_checklist(items: list[str]) -> str:
    if not items:
        return "- [ ] 暂无待办"
    return "\n".join(f"- [ ] {item}" for item in items)
