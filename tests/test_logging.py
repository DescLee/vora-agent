import json
from pathlib import Path

from manus_mini.logging import EventLogger
from manus_mini.models import TaskState
from manus_mini.reporter import Reporter
from manus_mini.reporter import render_task_report


def test_event_logger_writes_jsonl(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "runs")
    path = logger.record("run-1", {"type": "context_budget", "estimated_tokens": 10})

    assert path.exists()
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["run_id"] == "run-1"
    assert row["type"] == "context_budget"
    assert row["estimated_tokens"] == 10
    assert "ts" in row


def test_event_logger_redacts_sensitive_values(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "runs")
    path = logger.record(
        "run-1",
        {
            "type": "error",
            "message": "LLM_API_KEY=sk-live-secret",
            "nested": {"password": "password=abc123", "items": ["token=secret-token"]},
        },
    )

    raw = path.read_text(encoding="utf-8")
    row = json.loads(raw.strip())

    assert "sk-live-secret" not in raw
    assert "abc123" not in raw
    assert "secret-token" not in raw
    assert "[REDACTED]" in row["message"]
    assert "[REDACTED]" in row["nested"]["password"]
    assert "[REDACTED]" in row["nested"]["items"][0]


def test_reporter_writes_markdown_output(tmp_path: Path) -> None:
    reporter = Reporter(tmp_path / "outputs")

    path = reporter.write_markdown("report.md", "# Report\n\ncontent")

    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Report\n\ncontent"


def test_reporter_avoids_overwriting_existing_markdown_output(tmp_path: Path) -> None:
    reporter = Reporter(tmp_path / "outputs")

    first = reporter.write_markdown("report.md", "first")
    second = reporter.write_markdown("report.md", "second")

    assert first != second
    assert first.read_text(encoding="utf-8") == "first"
    assert second.read_text(encoding="utf-8") == "second"
    assert second.name == "report-1.md"


def test_task_report_chunks_long_user_input_and_result(tmp_path: Path) -> None:
    task = TaskState.create(goal="长文本", cwd=tmp_path)
    task.result = "R" * 25

    content = render_task_report(task, "U" * 25, chunk_size=10)

    assert "#### chunk 1" in content
    assert "#### chunk 2" in content
    assert "#### chunk 3" in content
    assert "UUUUUUUUUU" in content
    assert "RRRRRRRRRR" in content
