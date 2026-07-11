from __future__ import annotations

# ruff: noqa: E402

import json
import argparse
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

from vora.context import validate_tool_call_pairs
from vora.llm import LLMResult
from vora.memory import MemoryManager
from vora.models import Message, PendingConfirmation, PlanStep, SessionState, TaskState, ToolCall, TraceEvent
from vora.react import ReActLoop
from vora.reflection import ReflectionLoop
from vora.scheduler import ToolScheduler
from vora.session import SessionManager
from vora.tools.file_tools import ReadFileTool, WriteFileTool
from vora.tools.registry import ToolRegistry
from vora.tools.shell_tools import RunBashTool


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
        result = ReadFileTool().run(workspace=cwd, path="../outside.txt")
        assert result.ok is False
        assert result.error_code == "PATH_OUT_OF_WORKSPACE"


def eval_write_file_executes_directly_with_preview() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cwd = Path(directory)
        preview = WriteFileTool().preview(workspace=cwd, path="result.md", content="draft")
        assert preview.requires_confirmation is False
        result = WriteFileTool().run(workspace=cwd, path="result.md", content="draft")
        assert result.ok is True
        assert (cwd / "result.md").read_text(encoding="utf-8") == "draft"


def eval_dangerous_command_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        result = RunBashTool().run(workspace=Path(directory), command="sudo rm -rf /")
        assert result.ok is False
        assert result.error_code == "COMMAND_REJECTED"


def eval_report_write_requires_explicit_file_request() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cwd = Path(directory)
        task = TaskState.create(goal="请给我一份 AI Agent 框架行研摘要", cwd=cwd)
        call = ToolCall(
            id="call-report",
            name="run_bash",
            args={"command": "printf 'draft' > docs/report.md"},
        )

        result = ReActLoop()._report_write_precondition_error(call, task)

        assert result is not None
        assert result.error_code == "REPORT_WRITE_REQUIRES_EXPLICIT_REQUEST"
        assert result.data["path"] == "docs/report.md"


def eval_pending_confirmation_blocks_unrelated_messages() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cwd = Path(directory)
        manager = SessionManager(cwd)
        manager.current.pending_confirmation = PendingConfirmation(
            tool_name="run_bash",
            tool_call_id="call-command",
            tool_args={"command": "touch note.md"},
            summary="command modifies workspace files: touch",
            prompt="confirm command",
        )

        session = manager.handle_user_message("再问一个问题")

        assert session.pending_confirmation is not None
        assert session.pending_confirmation.tool_call_id == "call-command"
        assert session.active_task is None
        assert any(message.role == "system" for message in session.messages)


def eval_shell_pathlib_write_bytes_requires_test_first() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cwd = Path(directory)
        task = TaskState.create(goal="修改代码修复 bug", cwd=cwd)
        call = ToolCall(
            id="call-shell",
            name="run_bash",
            args={
                "command": "python -c \"from pathlib import Path; Path('app.py').write_bytes(b'new\\n')\"",
            },
        )

        result = ReActLoop()._code_change_precondition_error(call, task, SessionState.create(cwd=cwd))

        assert result is not None
        assert result.error_code == "CODE_CHANGE_REQUIRES_TEST_FIRST"
        assert result.data["path"] == "app.py"


CASE_RUNNERS: dict[str, Callable[[], None]] = {
    "reflection_rejects_unvalidated_code": eval_reflection_rejects_unvalidated_code,
    "reflection_accepts_validated_code": eval_reflection_accepts_validated_code,
    "non_code_task_bypasses_pytest_gate": eval_non_code_task_bypasses_pytest_gate,
    "sensitive_memory_is_rejected": eval_sensitive_memory_is_rejected,
    "tool_exchange_integrity_is_enforced": eval_tool_exchange_integrity_is_enforced,
    "scheduler_batches_read_only_tools": eval_scheduler_batches_read_only_tools,
    "path_escape_is_rejected": eval_path_escape_is_rejected,
    "write_file_executes_directly_with_preview": eval_write_file_executes_directly_with_preview,
    "dangerous_command_is_rejected": eval_dangerous_command_is_rejected,
    "report_write_requires_explicit_file_request": eval_report_write_requires_explicit_file_request,
    "pending_confirmation_blocks_unrelated_messages": eval_pending_confirmation_blocks_unrelated_messages,
    "shell_pathlib_write_bytes_requires_test_first": eval_shell_pathlib_write_bytes_requires_test_first,
}


def load_cases(path: Path = ROOT / "evals" / "cases.zh.json") -> list[EvalCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("eval case file must contain a JSON array")
    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("each eval case must be an object")
        case_id = str(item.get("id") or "").strip()
        category = str(item.get("category") or "").strip()
        target = str(item.get("target") or "").strip()
        if not case_id or not category or not target:
            raise ValueError("eval case requires id, category and target")
        if case_id in seen_ids:
            raise ValueError(f"duplicate eval case id: {case_id}")
        try:
            runner = CASE_RUNNERS[case_id]
        except KeyError as error:
            raise ValueError(f"eval case has no runner: {case_id}") from error
        seen_ids.add(case_id)
        cases.append(EvalCase(case_id, category, target, runner))
    missing = CASE_RUNNERS.keys() - seen_ids
    if missing:
        raise ValueError(f"eval runners are not declared: {', '.join(sorted(missing))}")
    return cases


def _markdown_report(report: dict[str, Any]) -> str:
    category_counts: dict[str, dict[str, int]] = {}
    for result in report["results"]:
        bucket = category_counts.setdefault(result["category"], {"passed": 0, "failed": 0})
        bucket["passed" if result["ok"] else "failed"] += 1
    lines = [
        "# Vora Eval 报告",
        "",
        f"- 总数：{report['total']}",
        f"- 通过：{report['passed']}",
        f"- 失败：{report['failed']}",
        "",
        "## 分类统计",
        "",
        "| 类别 | 通过 | 失败 |",
        "|---|---:|---:|",
    ]
    for category in sorted(category_counts):
        counts = category_counts[category]
        lines.append(f"| {category} | {counts['passed']} | {counts['failed']} |")
    lines.extend([
        "",
        "## 用例明细",
        "",
        "| 用例 | 类别 | 结果 | 目标 |",
        "|---|---|---:|---|",
    ])
    for result in report["results"]:
        status = "通过" if result["ok"] else f"失败：{result['error']}"
        lines.append(f"| `{result['id']}` | {result['category']} | {status} | {result['target']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 Vora 产品约束评测")
    parser.add_argument("--cases", type=Path, default=ROOT / "evals" / "cases.zh.json")
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--markdown-report", type=Path)
    args = parser.parse_args(argv)
    results = []
    for case in load_cases(args.cases):
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
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown_report:
        args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_report.write_text(_markdown_report(report), encoding="utf-8")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
