from pathlib import Path

from manus_mini.tools.automation_tools import extract_todos, generate_checklist, organize_notes
from manus_mini.tools.code_tools import apply_text_edit, propose_patch, scan_project
from manus_mini.tools.research_tools import collect_local_docs, generate_markdown_report, summarize_text


def test_research_tools_cover_local_docs_and_reporting(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("line1\nline2\nline3\nline4\nline5\nline6", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("guide", encoding="utf-8")

    docs = collect_local_docs(tmp_path)
    summary = summarize_text((tmp_path / "README.md").read_text(encoding="utf-8"))
    report = generate_markdown_report("Demo", {"Summary": summary}, sources=[path.name for path in docs])

    assert len(docs) == 2
    assert "line1" in summary
    assert "# Demo" in report
    assert "## Sources" in report


def test_code_tools_scan_edit_and_patch(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('old')\n", encoding="utf-8")

    assert "src/app.py" in scan_project(tmp_path)
    assert apply_text_edit("hello old", "old", "new") == "hello new"
    patch = propose_patch("a\n", "b\n", filename="app.py")

    assert "--- a/app.py" in patch
    assert "+++ b/app.py" in patch


def test_automation_tools_extract_and_format() -> None:
    todos = extract_todos("TODO: write docs\n- ship it\nignore this")
    notes = organize_notes(["alpha", "beta"])
    checklist = generate_checklist(["first", "second"])

    assert todos == ["TODO: write docs", "- ship it"]
    assert "- alpha" in notes
    assert "- [ ] first" in checklist
