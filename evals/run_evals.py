from __future__ import annotations

# ruff: noqa: E402

import json
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.as_posix() not in sys.path:
    sys.path.insert(0, SRC.as_posix())

from manus_mini.context import validate_tool_call_pairs
from manus_mini.llm import LLMResult
from manus_mini.memory import MemoryManager
from manus_mini.models import Message, PlanStep, SessionState, TaskState, ToolCall, TraceEvent
from manus_mini.reflection import ReflectionLoop
from manus_mini.scheduler import ToolScheduler
from manus_mini.tools.file_tools import ReadFileTool
from manus_mini.tools.registry import ToolRegistry


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    target: str
    run: Callable[[], None]


class FakeReactLoop:
    def __init__(self, draft: str) -> None:
        self.draft = draft

    def run(self, task: TaskState, session: SessionState) -> str:  # noqa: ARG002
        return self.draft


class AcceptingLLM:
    def complete_with_tools(self, messages: list[Any], tool_names: list[str]) -> LLMResult:  # noqa: ARG002
        return LLMResult(content='{"decision":"accept","reason":"eval accepted"}')


def eval_reflection_rejects_unvalidated_code() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cwd = Path(directory)
        task = TaskState.create(goal="修改代码修复 bug", cwd=cwd)
        task.plan = [PlanStep(description="修改实现", intent="code")]
        session = SessionState.create(cwd=cwd)

        result = ReflectionLoop(react_loop=FakeReactLoop("已修改代码"), llm=AcceptingLLM()).run(task, session)

        assert result.accepted is False
        assert result.decision == "local_update"
        assert "pytest" in result.reason
        assert "修改代码修复 bug" in result.reason


def eval_reflection_accepts_validated_code() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cwd = Path(directory)
        task = TaskState.create(goal="修改代码修复 bug", cwd=cwd)
        task.plan = [PlanStep(description="修改实现", intent="code")]
        task.trace_events.append(
            TraceEvent(
                phase="tool",
                message="Tool run_temp_script finished: ok",
                data={
                    "tool_name": "run_temp_script",
                    "ok": True,
                    "summary": "command exited 0",
                    "exit_code": 0,
                    "stdout": "1 passed",
                },
            )
        )
        session = SessionState.create(cwd=cwd)

        result = ReflectionLoop(react_loop=FakeReactLoop("已修改代码，并通过测试"), llm=AcceptingLLM()).run(task, session)

        pytest_events = [
            event.data
            for event in task.trace_events
            if event.phase == "reflection" and "case_content" in event.data
        ]
        assert result.accepted is True
        assert pytest_events
        assert pytest_events[-1]["ok"] is True


def eval_non_code_task_bypasses_pytest_gate() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cwd = Path(directory)
        task = TaskState.create(goal="总结这个项目", cwd=cwd)
        task.plan = [PlanStep(description="整理结论", intent="report")]
        session = SessionState.create(cwd=cwd)

        result = ReflectionLoop(react_loop=FakeReactLoop("项目总结"), llm=AcceptingLLM()).run(task, session)

        assert result.accepted is True
        assert result.reason == "non-code task accepted without pytest reflection gate"
        assert not any("case_content" in event.data for event in task.trace_events)


def eval_sensitive_memory_is_rejected() -> None:
    memory = MemoryManager(":memory:")
    try:
        item = memory.add_if_allowed(
            scope="user",
            kind="preference",
            content="请记住 API_KEY=secret-value",
            tags=["secret"],
        )
        assert item is None
        assert memory.search("API_KEY") == []
    finally:
        memory.close()


def eval_tool_exchange_integrity_is_enforced() -> None:
    validate_tool_call_pairs(
        [
            Message.agent("need tools", tool_call_ids=["call-a"]),
            Message.tool("ok", tool_call_id="call-a"),
        ]
    )
    try:
        validate_tool_call_pairs([Message.tool("orphan", tool_call_id="call-a")])
    except ValueError as error:
        assert "orphan tool_call_id" in str(error)
    else:
        raise AssertionError("orphan tool message should be rejected")


def eval_scheduler_batches_read_only_tools() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cwd = Path(directory)
        calls = [
            ToolCall(id="call-list", name="list_files", args={"workspace": cwd, "path": "."}),
            ToolCall(id="call-read", name="read_file", args={"workspace": cwd, "path": "README.md"}),
        ]
        batches = ToolScheduler(ToolRegistry()).plan(calls)
        assert len(batches) == 1
        assert {call.id for call in batches[0]} == {"call-list", "call-read"}


def eval_path_escape_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cwd = Path(directory)
        try:
            ReadFileTool().run(workspace=cwd, path="../outside.txt")
        except PermissionError as error:
            assert "PATH_OUT_OF_WORKSPACE" in str(error)
        else:
            raise AssertionError("path escape should be rejected")


CASES = [
    EvalCase(
        "reflection_rejects_unvalidated_code",
        "reflection",
        "代码任务没有测试证据时不能通过 Reflection",
        eval_reflection_rejects_unvalidated_code,
    ),
    EvalCase(
        "reflection_accepts_validated_code",
        "reflection",
        "代码任务有测试证据时执行 pytest gate 并通过",
        eval_reflection_accepts_validated_code,
    ),
    EvalCase(
        "non_code_task_bypasses_pytest_gate",
        "reflection",
        "非代码任务当前版本不运行 pytest gate",
        eval_non_code_task_bypasses_pytest_gate,
    ),
    EvalCase(
        "sensitive_memory_is_rejected",
        "memory",
        "敏感信息不会写入长期记忆",
        eval_sensitive_memory_is_rejected,
    ),
    EvalCase(
        "tool_exchange_integrity_is_enforced",
        "context",
        "tool_call 与 tool result 必须成组完整",
        eval_tool_exchange_integrity_is_enforced,
    ),
    EvalCase(
        "scheduler_batches_read_only_tools",
        "tools",
        "无依赖只读工具可进入同一并行批次",
        eval_scheduler_batches_read_only_tools,
    ),
    EvalCase(
        "path_escape_is_rejected",
        "security",
        "文件工具拒绝 workspace 外路径",
        eval_path_escape_is_rejected,
    ),
]


def main() -> int:
    results = []
    for case in CASES:
        try:
            case.run()
        except Exception as error:  # noqa: BLE001
            results.append(
                {
                    "id": case.case_id,
                    "category": case.category,
                    "target": case.target,
                    "ok": False,
                    "error": str(error) or error.__class__.__name__,
                }
            )
        else:
            results.append(
                {
                    "id": case.case_id,
                    "category": case.category,
                    "target": case.target,
                    "ok": True,
                    "error": "",
                }
            )

    passed = sum(1 for result in results if result["ok"])
    report = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
