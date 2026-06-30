from pathlib import Path

from manus_mini.models import ToolCall
from manus_mini.scheduler import ToolScheduler
from manus_mini.tools import ToolRegistry


def test_scheduler_batches_parallel_read_only_tools(tmp_path: Path) -> None:
    registry = ToolRegistry()
    scheduler = ToolScheduler(registry)

    calls = [
        ToolCall(
            id="list-1",
            name="list_files",
            args={"workspace": str(tmp_path)},
        ),
        ToolCall(
            id="read-1",
            name="read_file",
            args={"workspace": str(tmp_path), "path": "notes.txt"},
        ),
    ]

    batches = scheduler.plan(calls)

    assert len(batches) == 1
    assert [call.id for call in batches[0]] == ["list-1", "read-1"]


def test_scheduler_serializes_dependent_calls(tmp_path: Path) -> None:
    registry = ToolRegistry()
    scheduler = ToolScheduler(registry)

    calls = [
        ToolCall(
            id="read-1",
            name="read_file",
            args={"workspace": str(tmp_path), "path": "notes.txt"},
        ),
        ToolCall(
            id="read-2",
            name="read_file",
            args={"workspace": str(tmp_path), "path": "guide.txt"},
            depends_on=["read-1"],
        ),
    ]

    batches = scheduler.plan(calls)

    assert len(batches) == 2
    assert [call.id for call in batches[0]] == ["read-1"]
    assert [call.id for call in batches[1]] == ["read-2"]


def test_scheduler_serializes_resource_conflicts(tmp_path: Path) -> None:
    registry = ToolRegistry()
    scheduler = ToolScheduler(registry)

    calls = [
        ToolCall(
            id="write-1",
            name="write_file",
            args={"workspace": str(tmp_path), "path": "draft.txt", "content": "one"},
            risk_level="write",
            resource_keys=["draft.txt"],
        ),
        ToolCall(
            id="write-2",
            name="write_file",
            args={"workspace": str(tmp_path), "path": "draft.txt", "content": "two"},
            risk_level="write",
            resource_keys=["draft.txt"],
        ),
    ]

    batches = scheduler.plan(calls)

    assert len(batches) == 2
    assert [call.id for call in batches[0]] == ["write-1"]
    assert [call.id for call in batches[1]] == ["write-2"]
