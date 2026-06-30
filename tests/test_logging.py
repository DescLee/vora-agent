import json
from pathlib import Path

from manus_mini.logging import EventLogger
from manus_mini.reporter import Reporter


def test_event_logger_writes_jsonl(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "runs")
    path = logger.record("run-1", {"type": "context_budget", "estimated_tokens": 10})

    assert path.exists()
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["run_id"] == "run-1"
    assert row["type"] == "context_budget"
    assert row["estimated_tokens"] == 10
    assert "ts" in row


def test_reporter_writes_markdown_output(tmp_path: Path) -> None:
    reporter = Reporter(tmp_path / "outputs")

    path = reporter.write_markdown("report.md", "# Report\n\ncontent")

    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Report\n\ncontent"
