import json
import re
from pathlib import Path

from manus_mini.logging import (
    EventLogger,
    default_manus_home,
    project_memory_path,
    project_runs_dir,
    project_sessions_dir,
    project_storage_dir,
)
from manus_mini.models import TaskState
from manus_mini.reporter import Reporter
from manus_mini.reporter import render_task_report


def test_event_logger_writes_jsonl(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "runs", enabled=True)
    path = logger.record("session-1", "run-1", {"type": "context_budget", "estimated_tokens": 10})

    assert path.exists()
    assert re.match(r"^\d{8}-\d{6}-\d{6}-event\.jsonl$", path.name)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["session_id"] == "session-1"
    assert row["run_id"] == "run-1"
    assert row["type"] == "context_budget"
    assert row["estimated_tokens"] == 10
    assert "ts" in row


def test_event_logger_redacts_sensitive_values(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "runs", enabled=True)
    path = logger.record(
        "session-1",
        "run-1",
        {
            "type": "error",
            "message": "LLM_API_KEY=sk-live-secret",
            "nested": {"password": "password=abc123", "items": ["token=secret-token"]},
        },
    )

    raw = path.read_text(encoding="utf-8")
    row = json.loads(raw.strip())

    assert "sk-live-secret" in raw
    assert "abc123" in raw
    assert "secret-token" in raw
    assert row["message"] == "LLM_API_KEY=sk-live-secret"
    assert row["nested"]["password"] == "password=abc123"
    assert row["nested"]["items"][0] == "token=secret-token"


def test_event_logger_defaults_to_disabled_in_tests(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "runs")
    path = logger.record("session-1", "run-1", {"type": "context_budget", "estimated_tokens": 10})

    assert re.match(r"^\d{8}-\d{6}-\d{6}-event\.jsonl$", path.name)
    assert not path.exists()


def test_event_logger_defaults_to_user_manus_mini_runs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    logger = EventLogger()
    path = logger.record("session-1", "run-1", {"type": "context_budget"})

    assert logger.root == default_manus_home() / "runs"
    assert path.parent == default_manus_home() / "runs" / "session-1-run-1"


def test_project_storage_dirs_are_isolated_by_project_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    project_a = tmp_path / "workspace-a" / "same-name"
    project_b = tmp_path / "workspace-b" / "same-name"

    assert project_storage_dir(project_a) != project_storage_dir(project_b)
    assert project_storage_dir(project_a).parent == default_manus_home() / "projects"
    assert project_sessions_dir(project_a) == project_storage_dir(project_a) / "sessions"
    assert project_runs_dir(project_a) == project_storage_dir(project_a) / "runs"
    assert project_memory_path(project_a) == project_storage_dir(project_a) / "memory.db"


def test_event_logger_compacts_duplicate_llm_payload_fields(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "runs", enabled=True)
    request = {"messages": [{"role": "user", "content": "hi"}], "tool_names": []}
    response = {"choices": [{"message": {"content": "ok"}}]}

    path = logger.record(
        "session-1",
        "run-1",
        {
            "type": "llm_response",
            "stage": "react",
            "request": request,
            "response": response,
            "api_request_payload": request,
            "api_response_raw": response,
        },
    )

    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert "request" not in row
    assert "api_request_payload" not in row
    assert "api_response_raw" not in row
    assert row["response"] == response


def test_event_logger_compacts_reflection_observation_content(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "runs", enabled=True)

    path = logger.record(
        "session-1",
        "run-1",
        {
            "type": "reflection",
            "reflection_context": {
                "observations": [
                    {
                        "tool_call_id": "call-read",
                        "ok": True,
                        "summary": "read README.md",
                        "content": "x" * 600,
                    }
                ]
            },
        },
    )

    row = json.loads(path.read_text(encoding="utf-8").strip())
    observation = row["reflection_context"]["observations"][0]
    assert "content" not in observation
    assert observation["content_preview"] == "x" * 500
    assert observation["content_omitted"] is True


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
