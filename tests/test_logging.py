import json
from pathlib import Path

import pytest

from vora.logging import (
    EventLogger,
    default_vora_home,
    project_memory_path,
    project_logs_dir,
    project_outputs_dir,
    project_sessions_dir,
    project_storage_dir,
)
from vora.models import TaskState
from vora.reporter import Reporter
from vora.reporter import render_task_report


def test_event_logger_writes_jsonl(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "logs", enabled=True)
    path = logger.record("session-1", "run-1", {"type": "context_budget", "estimated_tokens": 10})

    assert path.exists()
    assert path == tmp_path / "logs" / "session-1" / "node.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["session_id"] == "session-1"
    assert row["run_id"] == "run-1"
    assert row["type"] == "context_budget"
    assert row["estimated_tokens"] == 10
    assert row["node_id"] == "run-1:node-0001"
    assert row["upstream_node_ids"] == []
    assert "ts" in row
    pipeline = json.loads((tmp_path / "logs" / "session-1" / "pipeline.jsonl").read_text(encoding="utf-8").strip())
    assert pipeline["node"] == "context_budget"
    assert pipeline["status"] == "recorded"
    assert pipeline["node_id"] == row["node_id"]
    assert pipeline["upstream_node_ids"] == []


def test_event_logger_writes_three_session_log_files(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "logs", enabled=True)

    first_path = logger.record("session-1", "run-1", {"type": "start"})
    second_path = logger.record("session-1", "run-2", {"type": "finish"})
    summary_path = logger.record_summary("session-1", "run-2", "用户输入", "执行结果", "done")

    assert first_path == second_path
    assert first_path == tmp_path / "logs" / "session-1" / "node.jsonl"
    assert summary_path == tmp_path / "logs" / "session-1" / "summary.jsonl"
    assert sorted(path.name for path in first_path.parent.iterdir()) == ["node.jsonl", "pipeline.jsonl", "summary.jsonl"]
    node_rows = [json.loads(line) for line in first_path.read_text(encoding="utf-8").splitlines()]
    pipeline_rows = [json.loads(line) for line in (first_path.parent / "pipeline.jsonl").read_text(encoding="utf-8").splitlines()]
    summary_rows = [json.loads(line) for line in summary_path.read_text(encoding="utf-8").splitlines()]
    assert [row["run_id"] for row in node_rows] == ["run-1", "run-2"]
    assert [row["type"] for row in node_rows] == ["start", "finish"]
    assert [row["node"] for row in pipeline_rows] == ["start", "finish"]
    assert [row["node_id"] for row in node_rows] == ["run-1:node-0001", "run-2:node-0001"]
    assert summary_rows[0]["user_input"] == "用户输入"
    assert summary_rows[0]["result"] == "执行结果"
    assert summary_rows[0]["final_node_id"] == "run-2:node-0001"


def test_event_logger_links_pipeline_and_node_rows_by_upstream_node_ids(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "logs", enabled=True)

    logger.record("session-1", "run-1", {"type": "user_input"})
    logger.record("session-1", "run-1", {"type": "llm_request", "stage": "planner"})
    logger.record("session-1", "run-1", {"type": "llm_response", "stage": "planner"})

    log_dir = tmp_path / "logs" / "session-1"
    node_rows = [json.loads(line) for line in (log_dir / "node.jsonl").read_text(encoding="utf-8").splitlines()]
    pipeline_rows = [json.loads(line) for line in (log_dir / "pipeline.jsonl").read_text(encoding="utf-8").splitlines()]

    assert [row["node_id"] for row in node_rows] == ["run-1:node-0001", "run-1:node-0002", "run-1:node-0003"]
    assert node_rows[1]["upstream_node_ids"] == ["run-1:node-0001"]
    assert node_rows[2]["upstream_node_ids"] == ["run-1:node-0002"]
    assert pipeline_rows[2]["node_id"] == node_rows[2]["node_id"]
    assert pipeline_rows[2]["upstream_node_ids"] == ["run-1:node-0002"]


def test_event_logger_redacts_sensitive_values(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "logs", enabled=True)
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

    assert "sk-live-secret" not in raw
    assert "abc123" not in raw
    assert "secret-token" not in raw
    assert row["message"] == "LLM_API_KEY=[REDACTED]"
    assert row["nested"]["password"] == "password=[REDACTED]"
    assert row["nested"]["items"][0] == "token=[REDACTED]"


def test_event_logger_redacts_summary_values(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "logs", enabled=True)

    path = logger.record_summary(
        "session-1",
        "run-1",
        "请记住 token=secret-token",
        "结果包含 password=abc123",
        "done",
    )

    raw = path.read_text(encoding="utf-8")
    row = json.loads(raw.strip())

    assert "secret-token" not in raw
    assert "abc123" not in raw
    assert row["user_input"] == "请记住 token=[REDACTED]"
    assert row["result"] == "结果包含 password=[REDACTED]"


def test_event_logger_rejects_session_id_path_traversal(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "logs", enabled=True)

    with pytest.raises(ValueError):
        logger.record("../outside", "run-1", {"type": "context_budget"})
    with pytest.raises(ValueError):
        logger.record_summary("../outside", "run-1", "input", "result", "done")

    assert not (tmp_path / "outside").exists()


def test_event_logger_refuses_symlinked_session_log_dir(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    outside_dir = tmp_path / "outside-logs"
    outside_dir.mkdir()
    (logs_dir / "session-1").symlink_to(outside_dir, target_is_directory=True)
    logger = EventLogger(logs_dir, enabled=True)

    with pytest.raises(OSError, match="symlink"):
        logger.record("session-1", "run-1", {"type": "context_budget"})
    with pytest.raises(OSError, match="symlink"):
        logger.record_summary("session-1", "run-1", "input", "result", "done")

    assert not (outside_dir / "node.jsonl").exists()
    assert not (outside_dir / "summary.jsonl").exists()


def test_event_logger_defaults_to_disabled_in_tests(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "logs")
    path = logger.record("session-1", "run-1", {"type": "context_budget", "estimated_tokens": 10})

    assert path == tmp_path / "logs" / "session-1" / "node.jsonl"
    assert not path.exists()


def test_event_logger_defaults_to_user_vora_logs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    logger = EventLogger()
    path = logger.record("session-1", "run-1", {"type": "context_budget"})

    assert logger.root == default_vora_home() / "logs"
    assert path.parent == default_vora_home() / "logs" / "session-1"


def test_project_storage_dirs_are_isolated_by_project_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    project_a = tmp_path / "workspace-a" / "same-name"
    project_b = tmp_path / "workspace-b" / "same-name"

    assert project_storage_dir(project_a) != project_storage_dir(project_b)
    assert project_storage_dir(project_a).parent == default_vora_home() / "projects"
    assert project_sessions_dir(project_a) == project_storage_dir(project_a) / "sessions"
    assert project_logs_dir(project_a) == project_storage_dir(project_a) / "logs"
    assert project_outputs_dir(project_a) == project_storage_dir(project_a) / "outputs"
    assert project_memory_path(project_a) == project_storage_dir(project_a) / "memory.db"


def test_project_storage_dir_falls_back_to_workspace_when_user_home_is_unwritable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("vora.logging.default_vora_home", lambda: tmp_path / "home")
    project = tmp_path / "workspace"

    original_mkdir = Path.mkdir

    def fake_mkdir(self, mode=0o777, parents=False, exist_ok=False):  # noqa: ANN001, FBT002
        if str(self).startswith(str(tmp_path / "home")):
            raise PermissionError("blocked")
        return original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    storage = project_storage_dir(project)

    assert storage == project / ".vora"


def test_project_storage_dir_falls_back_when_project_store_is_unwritable(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    projects_root = home / "projects"
    monkeypatch.setattr("vora.logging.default_vora_home", lambda: home)
    project = tmp_path / "workspace"

    original_mkdir = Path.mkdir

    def fake_mkdir(self, mode=0o777, parents=False, exist_ok=False):  # noqa: ANN001, FBT002
        if str(self).startswith(str(projects_root)) and self != projects_root:
            raise PermissionError("blocked project store")
        return original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    storage = project_storage_dir(project)

    assert storage == project / ".vora"


def test_event_logger_compacts_duplicate_llm_payload_fields(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "logs", enabled=True)
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


def test_event_logger_compacts_large_llm_request_messages(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "logs", enabled=True)
    long_content = "x" * 5000

    path = logger.record(
        "session-1",
        "run-1",
        {
            "type": "llm_request",
            "stage": "react",
            "request": {
                "messages": [{"role": "system", "content": long_content}],
                "tool_names": ["read_file"],
            },
        },
    )

    raw = path.read_text(encoding="utf-8")
    row = json.loads(raw.strip())
    message = row["request"]["messages"][0]
    assert len(raw) < 2500
    assert message["content_preview"] == "x" * 1200
    assert message["content_omitted"] is True
    assert "content" not in message


def test_event_logger_compacts_reflection_observation_content(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "logs", enabled=True)

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


def test_event_logger_keeps_only_recent_reflection_observations(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "logs", enabled=True)
    observations = [
        {"tool_call_id": f"call-{index}", "ok": True, "summary": f"event {index}"}
        for index in range(30)
    ]

    path = logger.record(
        "session-1",
        "run-1",
        {
            "type": "reflection",
            "reflection_context": {"observations": observations},
        },
    )

    row = json.loads(path.read_text(encoding="utf-8").strip())
    compacted = row["reflection_context"]["observations"]
    assert len(compacted) == 12
    assert compacted[0]["tool_call_id"] == "call-18"
    assert compacted[-1]["tool_call_id"] == "call-29"
    assert row["reflection_context"]["observations_omitted"] == 18


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
