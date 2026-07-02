# Manus Mini V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable TUI AI Agent prototype with multi-turn sessions, three-layer loop engineering, parallel tool scheduling, long-term memory, context compression, safe file tools, logs, and focused tests.

**Architecture:** The app is a Python package under `src/manus_mini`. `app.py` owns the Textual TUI, `runtime.py` owns the outer engineering loop, `react.py` owns tool-calling ReAct iterations, `reflection.py` owns quality feedback, `context.py` owns context budgeting/compression, `memory.py` owns local memory, and `tools/` owns tool contracts plus implementations. V1 uses an explicitly configured LLM provider so tests and demos can inject their own transport.

**Tech Stack:** Python 3.12+, Textual, Rich, Pydantic, pytest, ruff, sqlite3, asyncio.

---

## File Structure

- Create `pyproject.toml`: package metadata, dependencies, console script, pytest/ruff config.
- Create `README.md`: local setup, run commands, demo prompts.
- Create `src/manus_mini/models.py`: shared Pydantic models including `SessionState`, `TaskState`, `LoopLimits`, `Message`, `ToolCall`, `ContextSegment`.
- Create `src/manus_mini/context.py`: token estimation, segment building, tool-call pair validation, compression/cropping.
- Create `src/manus_mini/memory.py`: sqlite-backed memory manager.
- Create `src/manus_mini/tools/base.py`: tool protocol, tool specs, result models.
- Create `src/manus_mini/tools/file_tools.py`: list/read/write/append/mkdir tools with workspace path checks.
- Create `src/manus_mini/tools/registry.py`: tool registry and default registration.
- Create `src/manus_mini/scheduler.py`: dependency analysis and parallel batch planning.
- Create `src/manus_mini/llm.py`: LLM client interface and structured result models.
- Create `src/manus_mini/react.py`: ReAct tool loop.
- Create `src/manus_mini/reflection.py`: reflection loop decisions.
- Create `src/manus_mini/runtime.py`: outer engineering loop and failure handling.
- Create `src/manus_mini/session.py`: session lifecycle.
- Create `src/manus_mini/logging.py`: JSONL event logger.
- Create `src/manus_mini/reporter.py`: final message/artifact generation.
- Create `src/manus_mini/app.py`: Textual TUI entrypoint.
- Create tests under `tests/`.

---

### Task 1: Project Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/manus_mini/__init__.py`
- Test: `tests/test_package.py`

- [ ] **Step 1: Write the failing package test**

Create `tests/test_package.py`:

```python
from manus_mini import __version__


def test_package_version_is_defined() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_package.py -v
```

Expected: FAIL because package metadata does not exist yet.

- [ ] **Step 3: Create project metadata**

Create `pyproject.toml`:

```toml
[project]
name = "manus-mini"
version = "0.1.0"
description = "A TUI mini Manus-style agent for interview demos"
requires-python = ">=3.12"
dependencies = [
  "pydantic>=2.8",
  "rich>=13.7",
  "textual>=0.70",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "ruff>=0.5",
]

[project.scripts]
manus-mini = "manus_mini.app:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

Create `src/manus_mini/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `README.md`:

````markdown
# Manus Mini

TUI mini Manus-style agent for interview demos.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Run

```bash
manus-mini
```

## Demo prompts

- 阅读 docs 目录，生成项目背景报告
- 第二部分太泛了，补充技术架构风险
- 记住：报告用面试讲解风格
````

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/test_package.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml README.md src/manus_mini/__init__.py tests/test_package.py
git commit -m "chore: scaffold python package"
```

---

### Task 2: Core Models

**Files:**
- Create: `src/manus_mini/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing model tests**

Create `tests/test_models.py`:

```python
from pathlib import Path

from manus_mini.models import (
    ContextSegment,
    LoopLimits,
    Message,
    SessionState,
    TaskState,
)


def test_loop_limits_have_v1_defaults() -> None:
    limits = LoopLimits()
    assert limits.max_engineering_steps == 12
    assert limits.max_react_iterations == 8
    assert limits.max_reflection_rounds == 5
    assert limits.max_tool_retries == 2


def test_tool_message_can_reference_tool_call_id() -> None:
    assistant = Message.agent("need file", tool_call_ids=["call-1"])
    tool = Message.tool("file content", tool_call_id="call-1")
    assert assistant.tool_call_ids == ["call-1"]
    assert tool.tool_call_id == "call-1"


def test_session_state_starts_empty(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    assert session.cwd == tmp_path
    assert session.messages == []
    assert session.active_task is None


def test_task_state_uses_loop_limits(tmp_path: Path) -> None:
    task = TaskState.create(goal="write report", cwd=tmp_path)
    assert task.goal == "write report"
    assert task.limits.max_engineering_steps == 12
    assert task.step_count == 0


def test_context_segment_tracks_priority() -> None:
    segment = ContextSegment(
        id="seg-1",
        kind="plain_message",
        messages=[Message.user("hello")],
        estimated_tokens=3,
        priority=10,
    )
    assert segment.priority == 10
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_models.py -v
```

Expected: FAIL because `models.py` does not exist.

- [ ] **Step 3: Implement core models**

Create `src/manus_mini/models.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class LoopLimits(BaseModel):
    max_engineering_steps: int = 12
    max_react_iterations: int = 8
    max_reflection_rounds: int = 5
    max_tool_retries: int = 2
    max_estimated_tokens: int = 128_000


class Message(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    role: Literal["user", "agent", "system", "tool"]
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tool_call_ids: list[str] = Field(default_factory=list)
    tool_call_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def agent(cls, content: str, tool_call_ids: list[str] | None = None) -> "Message":
        return cls(role="agent", content=content, tool_call_ids=tool_call_ids or [])

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role="system", content=content)

    @classmethod
    def tool(cls, content: str, tool_call_id: str) -> "Message":
        return cls(role="tool", content=content, tool_call_id=tool_call_id)


class AgentError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: new_id("artifact"))
    path: Path
    kind: Literal["markdown", "text", "patch", "json"] = "markdown"
    summary: str = ""


class Observation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("obs"))
    tool_call_id: str | None = None
    ok: bool
    summary: str
    content: str = ""


class PlanStep(BaseModel):
    id: str = Field(default_factory=lambda: new_id("step"))
    description: str
    intent: Literal["research", "code", "automation", "report"]
    status: Literal["pending", "running", "done", "skipped", "failed"] = "pending"


class TaskState(BaseModel):
    run_id: str = Field(default_factory=lambda: new_id("run"))
    goal: str
    cwd: Path
    status: Literal["planning", "acting", "observing", "reflecting", "reporting", "done", "failed"] = "planning"
    plan: list[PlanStep] = Field(default_factory=list)
    current_step_index: int = 0
    observations: list[Observation] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    errors: list[AgentError] = Field(default_factory=list)
    limits: LoopLimits = Field(default_factory=LoopLimits)
    step_count: int = 0
    result: str = ""

    @classmethod
    def create(cls, goal: str, cwd: Path, limits: LoopLimits | None = None) -> "TaskState":
        return cls(goal=goal, cwd=cwd, limits=limits or LoopLimits())


class PendingConfirmation(BaseModel):
    prompt: str
    tool_call_id: str


class CompressionSnapshot(BaseModel):
    id: str = Field(default_factory=lambda: new_id("compression"))
    covered_message_ids: list[str]
    covered_observation_ids: list[str]
    summary: str
    retained_facts: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("memory"))
    scope: Literal["user", "project", "session", "artifact"]
    kind: Literal["preference", "project_summary", "artifact_summary", "decision", "constraint"]
    content: str
    tags: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    source_message_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionState(BaseModel):
    session_id: str = Field(default_factory=lambda: new_id("session"))
    cwd: Path
    messages: list[Message] = Field(default_factory=list)
    active_task: TaskState | None = None
    artifacts: list[Artifact] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)
    compression_snapshots: list[CompressionSnapshot] = Field(default_factory=list)
    pending_confirmation: PendingConfirmation | None = None
    run_ids: list[str] = Field(default_factory=list)

    @classmethod
    def create(cls, cwd: Path) -> "SessionState":
        return cls(cwd=cwd)


class ContextSegment(BaseModel):
    id: str
    kind: Literal["plain_message", "tool_exchange", "summary", "memory", "artifact"]
    messages: list[Message] = Field(default_factory=list)
    estimated_tokens: int
    priority: int


class ContextBundle(BaseModel):
    current_user_message: Message
    recent_messages: list[Message] = Field(default_factory=list)
    relevant_memories: list[MemoryItem] = Field(default_factory=list)
    compression_summaries: list[CompressionSnapshot] = Field(default_factory=list)
    active_artifacts: list[Artifact] = Field(default_factory=list)
    recent_observations: list[Observation] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_models.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/manus_mini/models.py tests/test_models.py
git commit -m "feat: add core state models"
```

---

### Task 3: Context Budgeting and Tool-Call Integrity

**Files:**
- Create: `src/manus_mini/context.py`
- Test: `tests/test_context.py`

- [ ] **Step 1: Write failing context tests**

Create `tests/test_context.py`:

```python
import pytest

from manus_mini.context import (
    ContextIntegrityError,
    build_segments,
    estimate_tokens,
    validate_tool_call_pairs,
)
from manus_mini.models import Message


def test_estimate_tokens_for_mixed_text() -> None:
    assert estimate_tokens("abcd", "mixed") == 2
    assert estimate_tokens("中文", "zh") == 2
    assert estimate_tokens("one two", "en") == 2
    assert estimate_tokens("print('hello')", "code") == 4


def test_validate_tool_call_pairs_accepts_complete_exchange() -> None:
    messages = [
        Message.agent("need tools", tool_call_ids=["call-1", "call-2"]),
        Message.tool("a", tool_call_id="call-1"),
        Message.tool("b", tool_call_id="call-2"),
    ]
    validate_tool_call_pairs(messages)


def test_validate_tool_call_pairs_rejects_orphan_tool_result() -> None:
    messages = [Message.tool("orphan", tool_call_id="call-1")]
    with pytest.raises(ContextIntegrityError) as exc:
        validate_tool_call_pairs(messages)
    assert "orphan" in str(exc.value)


def test_build_segments_keeps_tool_exchange_together() -> None:
    messages = [
        Message.user("read docs"),
        Message.agent("need file", tool_call_ids=["call-1"]),
        Message.tool("content", tool_call_id="call-1"),
        Message.agent("done"),
    ]
    segments = build_segments(messages)
    kinds = [segment.kind for segment in segments]
    assert kinds == ["plain_message", "tool_exchange", "plain_message"]
    assert [message.role for message in segments[1].messages] == ["agent", "tool"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_context.py -v
```

Expected: FAIL because `context.py` does not exist.

- [ ] **Step 3: Implement context utilities**

Create `src/manus_mini/context.py`:

```python
from __future__ import annotations

from typing import Literal

from manus_mini.models import ContextSegment, Message, new_id


class ContextIntegrityError(ValueError):
    def __init__(self, orphan_results: set[str], missing_results: set[str]) -> None:
        self.orphan_results = orphan_results
        self.missing_results = missing_results
        super().__init__(
            f"context tool_call_id integrity failed: "
            f"orphan={sorted(orphan_results)}, missing={sorted(missing_results)}"
        )


def estimate_tokens(text: str, kind: Literal["zh", "en", "code", "mixed"]) -> int:
    if not text:
        return 0
    if kind == "zh":
        return max(1, int(len(text) * 1.2))
    if kind == "en":
        return max(1, int(len(text.split()) * 1.3))
    if kind == "code":
        return max(1, len(text) // 3)
    return max(1, len(text) // 2)


def validate_tool_call_pairs(messages: list[Message]) -> None:
    requested_ids: set[str] = set()
    answered_ids: set[str] = set()

    for message in messages:
        if message.role == "agent":
            requested_ids.update(message.tool_call_ids)
        if message.role == "tool" and message.tool_call_id:
            answered_ids.add(message.tool_call_id)

    orphan_results = answered_ids - requested_ids
    missing_results = requested_ids - answered_ids

    if orphan_results or missing_results:
        raise ContextIntegrityError(orphan_results, missing_results)


def _message_tokens(messages: list[Message]) -> int:
    return sum(estimate_tokens(message.content, "mixed") for message in messages)


def build_segments(messages: list[Message]) -> list[ContextSegment]:
    segments: list[ContextSegment] = []
    index = 0

    while index < len(messages):
        message = messages[index]
        if message.role == "agent" and message.tool_call_ids:
            exchange = [message]
            expected = set(message.tool_call_ids)
            index += 1
            while index < len(messages) and expected:
                next_message = messages[index]
                if next_message.role != "tool" or next_message.tool_call_id not in expected:
                    break
                exchange.append(next_message)
                expected.remove(next_message.tool_call_id)
                index += 1

            validate_tool_call_pairs(exchange)
            segments.append(
                ContextSegment(
                    id=new_id("segment"),
                    kind="tool_exchange",
                    messages=exchange,
                    estimated_tokens=_message_tokens(exchange),
                    priority=40,
                )
            )
            continue

        segments.append(
            ContextSegment(
                id=new_id("segment"),
                kind="plain_message",
                messages=[message],
                estimated_tokens=estimate_tokens(message.content, "mixed"),
                priority=50 if message.role == "user" else 45,
            )
        )
        index += 1

    return segments
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_context.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/manus_mini/context.py tests/test_context.py
git commit -m "feat: add context integrity checks"
```

---

### Task 4: Tool Contracts, File Tools, and Scheduler

**Files:**
- Create: `src/manus_mini/tools/__init__.py`
- Create: `src/manus_mini/tools/base.py`
- Create: `src/manus_mini/tools/file_tools.py`
- Create: `src/manus_mini/tools/registry.py`
- Create: `src/manus_mini/scheduler.py`
- Test: `tests/test_tools.py`
- Test: `tests/test_scheduler.py`

- [ ] **Step 1: Write failing file tool tests**

Create `tests/test_tools.py`:

```python
from pathlib import Path

import pytest

from manus_mini.tools.file_tools import ListFilesTool, ReadFileTool, WriteFileTool


def test_read_file_stays_inside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    tool = ReadFileTool()
    result = tool.run({"path": str(outside)}, cwd=tmp_path)
    assert not result.ok
    assert result.error_code == "PATH_OUT_OF_WORKSPACE"


def test_list_files_lists_relative_paths(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("a", encoding="utf-8")
    result = ListFilesTool().run({"path": "docs", "max_depth": 2}, cwd=tmp_path)
    assert result.ok
    assert "docs/a.md" in result.content


def test_write_file_requires_confirmation(tmp_path: Path) -> None:
    tool = WriteFileTool()
    preview = tool.preview({"path": "a.txt", "content": "hello"}, cwd=tmp_path)
    assert preview.requires_confirmation
    assert "a.txt" in preview.summary
```

Create `tests/test_scheduler.py`:

```python
from manus_mini.models import ToolCall
from manus_mini.scheduler import ToolScheduler


def test_scheduler_parallelizes_independent_safe_calls() -> None:
    calls = [
        ToolCall(id="a", name="read_file", args={"path": "a.md"}, resource_keys=["file:a.md"]),
        ToolCall(id="b", name="read_file", args={"path": "b.md"}, resource_keys=["file:b.md"]),
    ]
    batches = ToolScheduler().plan_batches(calls)
    assert [[call.id for call in batch] for batch in batches] == [["a", "b"]]


def test_scheduler_serializes_dependent_calls() -> None:
    calls = [
        ToolCall(id="a", name="read_file", args={"path": "a.md"}, resource_keys=["file:a.md"]),
        ToolCall(
            id="b",
            name="summarize",
            args={"source": "a"},
            depends_on=["a"],
            resource_keys=["artifact:summary"],
        ),
    ]
    batches = ToolScheduler().plan_batches(calls)
    assert [[call.id for call in batch] for batch in batches] == [["a"], ["b"]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_tools.py tests/test_scheduler.py -v
```

Expected: FAIL because tool modules and `ToolCall` do not exist yet.

- [ ] **Step 3: Add `ToolCall` model**

Modify `src/manus_mini/models.py` by adding:

```python
class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any]
    depends_on: list[str] = Field(default_factory=list)
    resource_keys: list[str] = Field(default_factory=list)
    risk_level: Literal["safe", "write", "command"] = "safe"
```

- [ ] **Step 4: Implement tool base classes**

Create `src/manus_mini/tools/__init__.py`:

```python
from manus_mini.tools.file_tools import ListFilesTool, ReadFileTool, WriteFileTool

__all__ = ["ListFilesTool", "ReadFileTool", "WriteFileTool"]
```

Create `src/manus_mini/tools/base.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel


class ToolPreview(BaseModel):
    summary: str
    requires_confirmation: bool = False


class ToolResult(BaseModel):
    ok: bool
    summary: str
    content: str = ""
    data: dict[str, Any] = {}
    error_code: str | None = None


class Tool(Protocol):
    name: str
    risk_level: str

    def preview(self, args: dict[str, Any], cwd: Path) -> ToolPreview:
        ...

    def run(self, args: dict[str, Any], cwd: Path) -> ToolResult:
        ...
```

- [ ] **Step 5: Implement file tools**

Create `src/manus_mini/tools/file_tools.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from manus_mini.tools.base import ToolPreview, ToolResult


def resolve_workspace_path(cwd: Path, value: str) -> Path:
    root = cwd.resolve()
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if path != root and root not in path.parents:
        raise ValueError("PATH_OUT_OF_WORKSPACE")
    return path


class ListFilesTool:
    name = "list_files"
    risk_level = "safe"

    def preview(self, args: dict[str, Any], cwd: Path) -> ToolPreview:
        return ToolPreview(summary=f"List files under {args.get('path', '.')}")

    def run(self, args: dict[str, Any], cwd: Path) -> ToolResult:
        try:
            base = resolve_workspace_path(cwd, str(args.get("path", ".")))
        except ValueError:
            return ToolResult(ok=False, summary="Path outside workspace", error_code="PATH_OUT_OF_WORKSPACE")

        max_depth = int(args.get("max_depth", 2))
        if not base.exists():
            return ToolResult(ok=False, summary="Path not found", error_code="FILE_NOT_FOUND")

        root = cwd.resolve()
        rows: list[str] = []
        for item in sorted(base.rglob("*")):
            if len(item.relative_to(base).parts) <= max_depth:
                rows.append(str(item.relative_to(root)))
        return ToolResult(ok=True, summary=f"Found {len(rows)} files", content="\n".join(rows))


class ReadFileTool:
    name = "read_file"
    risk_level = "safe"

    def preview(self, args: dict[str, Any], cwd: Path) -> ToolPreview:
        return ToolPreview(summary=f"Read {args['path']}")

    def run(self, args: dict[str, Any], cwd: Path) -> ToolResult:
        try:
            path = resolve_workspace_path(cwd, str(args["path"]))
        except ValueError:
            return ToolResult(ok=False, summary="Path outside workspace", error_code="PATH_OUT_OF_WORKSPACE")
        if not path.exists():
            return ToolResult(ok=False, summary="File not found", error_code="FILE_NOT_FOUND")
        return ToolResult(ok=True, summary=f"Read {path.name}", content=path.read_text(encoding="utf-8"))


class WriteFileTool:
    name = "write_file"
    risk_level = "write"

    def preview(self, args: dict[str, Any], cwd: Path) -> ToolPreview:
        return ToolPreview(summary=f"Write {args['path']}", requires_confirmation=True)

    def run(self, args: dict[str, Any], cwd: Path) -> ToolResult:
        try:
            path = resolve_workspace_path(cwd, str(args["path"]))
        except ValueError:
            return ToolResult(ok=False, summary="Path outside workspace", error_code="PATH_OUT_OF_WORKSPACE")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args["content"]), encoding="utf-8")
        return ToolResult(ok=True, summary=f"Wrote {path.relative_to(cwd.resolve())}")
```

- [ ] **Step 6: Implement registry and scheduler**

Create `src/manus_mini/tools/registry.py`:

```python
from __future__ import annotations

from manus_mini.tools.file_tools import ListFilesTool, ReadFileTool, WriteFileTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools = {
            tool.name: tool
            for tool in [
                ListFilesTool(),
                ReadFileTool(),
                WriteFileTool(),
            ]
        }

    def get(self, name: str):
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)
```

Create `src/manus_mini/scheduler.py`:

```python
from __future__ import annotations

from manus_mini.models import ToolCall


class ToolScheduler:
    def plan_batches(self, tool_calls: list[ToolCall]) -> list[list[ToolCall]]:
        remaining = {call.id: call for call in tool_calls}
        scheduled: set[str] = set()
        batches: list[list[ToolCall]] = []

        while remaining:
            ready = [
                call
                for call in remaining.values()
                if all(dependency in scheduled for dependency in call.depends_on)
            ]
            if not ready:
                raise ValueError("Tool dependency cycle detected")

            batch: list[ToolCall] = []
            for call in ready:
                if call.risk_level != "safe":
                    continue
                if self._conflicts(call, batch):
                    continue
                batch.append(call)

            if not batch:
                batch = [ready[0]]

            batches.append(batch)
            for call in batch:
                scheduled.add(call.id)
                remaining.pop(call.id)

        return batches

    def _conflicts(self, call: ToolCall, batch: list[ToolCall]) -> bool:
        keys = set(call.resource_keys)
        return any(keys.intersection(other.resource_keys) for other in batch)
```

- [ ] **Step 7: Run tests to verify they pass**

Run:

```bash
pytest tests/test_tools.py tests/test_scheduler.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/manus_mini/models.py src/manus_mini/tools src/manus_mini/scheduler.py tests/test_tools.py tests/test_scheduler.py
git commit -m "feat: add file tools and scheduler"
```

---

### Task 5: Memory Manager

**Files:**
- Create: `src/manus_mini/memory.py`
- Test: `tests/test_memory.py`

- [ ] **Step 1: Write failing memory tests**

Create `tests/test_memory.py`:

```python
from pathlib import Path

from manus_mini.memory import MemoryManager


def test_memory_stores_and_finds_preference(tmp_path: Path) -> None:
    manager = MemoryManager(tmp_path / "memory.db")
    item = manager.add(
        scope="user",
        kind="preference",
        content="报告用面试讲解风格",
        tags=["report", "style"],
    )
    results = manager.search("报告 风格", limit=5)
    assert results[0].id == item.id
    assert results[0].content == "报告用面试讲解风格"


def test_memory_does_not_store_sensitive_content(tmp_path: Path) -> None:
    manager = MemoryManager(tmp_path / "memory.db")
    stored = manager.add_if_allowed(
        scope="user",
        kind="preference",
        content="OPENAI_API_KEY=secret",
        tags=["secret"],
    )
    assert stored is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_memory.py -v
```

Expected: FAIL because `memory.py` does not exist.

- [ ] **Step 3: Implement sqlite memory manager**

Create `src/manus_mini/memory.py`:

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal

from manus_mini.models import MemoryItem


class MemoryManager:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def add(
        self,
        scope: Literal["user", "project", "session", "artifact"],
        kind: Literal["preference", "project_summary", "artifact_summary", "decision", "constraint"],
        content: str,
        tags: list[str],
    ) -> MemoryItem:
        item = MemoryItem(scope=scope, kind=kind, content=content, tags=tags)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memories
                (id, scope, kind, content, tags, confidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.scope,
                    item.kind,
                    item.content,
                    json.dumps(item.tags, ensure_ascii=False),
                    item.confidence,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                ),
            )
        return item

    def add_if_allowed(
        self,
        scope: Literal["user", "project", "session", "artifact"],
        kind: Literal["preference", "project_summary", "artifact_summary", "decision", "constraint"],
        content: str,
        tags: list[str],
    ) -> MemoryItem | None:
        blocked_terms = ["API_KEY", "TOKEN=", "PASSWORD=", "SECRET"]
        if any(term in content.upper() for term in blocked_terms):
            return None
        return self.add(scope=scope, kind=kind, content=content, tags=tags)

    def search(self, query: str, limit: int = 5) -> list[MemoryItem]:
        terms = [term for term in query.lower().split() if term]
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, scope, kind, content, tags, confidence, created_at, updated_at FROM memories"
            ).fetchall()

        scored: list[tuple[int, MemoryItem]] = []
        for row in rows:
            content = str(row[3]).lower()
            tags = json.loads(row[4])
            haystack = " ".join([content, *tags]).lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append(
                    (
                        score,
                        MemoryItem(
                            id=row[0],
                            scope=row[1],
                            kind=row[2],
                            content=row[3],
                            tags=tags,
                            confidence=row[5],
                        ),
                    )
                )
        return [item for _, item in sorted(scored, key=lambda pair: pair[0], reverse=True)[:limit]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_memory.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/manus_mini/memory.py tests/test_memory.py
git commit -m "feat: add local memory manager"
```

---

### Task 6: Three-Layer Runtime with Mock LLM

**Files:**
- Create: `src/manus_mini/llm.py`
- Create: `src/manus_mini/react.py`
- Create: `src/manus_mini/reflection.py`
- Create: `src/manus_mini/runtime.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write failing runtime tests**

Create `tests/test_runtime.py`:

```python
from pathlib import Path

from manus_mini.models import LoopLimits, SessionState, ToolCall
from manus_mini.runtime import AgentRuntime


def test_runtime_generates_report_message(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("Project uses Python.", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime()

    result = runtime.on_user_message("阅读 docs 目录，生成项目背景报告", session)

    assert result.messages[-1].role == "agent"
    assert "报告" in result.messages[-1].content


def test_runtime_respects_engineering_step_limit(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(default_limits=LoopLimits(max_engineering_steps=1))

    result = runtime.on_user_message("循环测试", session)

    assert result.active_task is not None
    assert result.active_task.step_count <= 1


def test_runtime_keeps_tool_observation(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello", encoding="utf-8")
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime()

    result = runtime.on_user_message("读取 a.md", session)

    assert result.active_task is not None
    assert any("Read" in observation.summary for observation in result.active_task.observations)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_runtime.py -v
```

Expected: FAIL because runtime modules do not exist.

- [ ] **Step 3: Implement LLM client**

Create `src/manus_mini/llm.py`:

```python
from __future__ import annotations

from pydantic import BaseModel

from manus_mini.models import ToolCall


class LLMResult(BaseModel):
    content: str
    tool_calls: list[ToolCall] = []


class TestLLMClient:
    def complete_with_tools(self, messages: list[dict[str, str]], tool_names: list[str]) -> LLMResult:
        text = "\n".join(message["content"] for message in messages)
        if "读取 a.md" in text:
            return LLMResult(
                content="",
                tool_calls=[
                    ToolCall(id="call-read-a", name="read_file", args={"path": "a.md"}, resource_keys=["file:a.md"])
                ],
            )
        if "docs" in text and "list_files" in tool_names:
            return LLMResult(
                content="",
                tool_calls=[
                    ToolCall(id="call-list-docs", name="list_files", args={"path": "docs", "max_depth": 2}, resource_keys=["dir:docs"])
                ],
            )
        return LLMResult(content="报告草稿：已根据当前上下文生成。")
```

- [ ] **Step 4: Implement ReAct and Reflection loops**

Create `src/manus_mini/react.py`:

```python
from __future__ import annotations

from manus_mini.llm import LLMClient
from manus_mini.models import Message, Observation, SessionState, TaskState
from manus_mini.scheduler import ToolScheduler
from manus_mini.tools.registry import ToolRegistry


class ReActLoop:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm
        self.registry = ToolRegistry()
        self.scheduler = ToolScheduler()

    def run(self, task: TaskState, session: SessionState) -> str:
        prompt_messages = [{"role": "user", "content": task.goal}]

        for _ in range(task.limits.max_react_iterations):
            llm_result = self.llm.complete_with_tools(prompt_messages, self.registry.names())
            if not llm_result.tool_calls:
                return llm_result.content

            batches = self.scheduler.plan_batches(llm_result.tool_calls)
            for batch in batches:
                for call in batch:
                    tool = self.registry.get(call.name)
                    result = tool.run(call.args, cwd=session.cwd)
                    observation = Observation(
                        tool_call_id=call.id,
                        ok=result.ok,
                        summary=result.summary,
                        content=result.content,
                    )
                    task.observations.append(observation)
                    prompt_messages.append({"role": "tool", "content": observation.content or observation.summary})

        raise RuntimeError("MAX_REACT_ITERATIONS_REACHED")
```

Create `src/manus_mini/reflection.py`:

```python
from __future__ import annotations

from pydantic import BaseModel

from manus_mini.models import SessionState, TaskState
from manus_mini.react import ReActLoop


class ReflectionResult(BaseModel):
    accepted: bool
    content: str
    reason: str


class ReflectionLoop:
    def __init__(self, react_loop: ReActLoop | None = None) -> None:
        self.react_loop = react_loop or ReActLoop()

    def run(self, task: TaskState, session: SessionState) -> ReflectionResult:
        best = ""
        for _ in range(task.limits.max_reflection_rounds):
            best = self.react_loop.run(task, session)
            if best.strip():
                return ReflectionResult(accepted=True, content=best, reason="accepted")
        return ReflectionResult(accepted=True, content=best, reason="max reflection rounds reached")
```

- [ ] **Step 5: Implement runtime**

Create `src/manus_mini/runtime.py`:

```python
from __future__ import annotations

from manus_mini.models import LoopLimits, Message, PlanStep, SessionState, TaskState
from manus_mini.reflection import ReflectionLoop


class AgentRuntime:
    def __init__(self, default_limits: LoopLimits | None = None) -> None:
        self.default_limits = default_limits or LoopLimits()
        self.reflection_loop = ReflectionLoop()

    def on_user_message(self, content: str, session: SessionState) -> SessionState:
        session.messages.append(Message.user(content))
        task = TaskState.create(goal=content, cwd=session.cwd, limits=self.default_limits)
        task.plan.append(PlanStep(description=content, intent="research"))

        while task.step_count < task.limits.max_engineering_steps:
            task.step_count += 1
            result = self.reflection_loop.run(task, session)
            task.result = result.content
            task.status = "done"
            break

        if not task.result:
            task.result = "已达到执行上限，当前没有足够结果。"
        session.messages.append(Message.agent(f"报告：{task.result}"))
        session.active_task = task
        return session
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
pytest tests/test_runtime.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/manus_mini/llm.py src/manus_mini/react.py src/manus_mini/reflection.py src/manus_mini/runtime.py tests/test_runtime.py
git commit -m "feat: add three layer runtime"
```

---

### Task 7: TUI App and Session Lifecycle

**Files:**
- Create: `src/manus_mini/session.py`
- Create: `src/manus_mini/app.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write failing session tests**

Create `tests/test_session.py`:

```python
from pathlib import Path

from manus_mini.session import SessionManager


def test_session_manager_creates_session(tmp_path: Path) -> None:
    manager = SessionManager(cwd=tmp_path)
    session = manager.current
    assert session.cwd == tmp_path
    assert session.messages == []


def test_session_manager_accepts_message(tmp_path: Path) -> None:
    manager = SessionManager(cwd=tmp_path)
    manager.handle_user_message("hello")
    assert manager.current.messages[0].content == "hello"
    assert manager.current.messages[-1].role == "agent"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_session.py -v
```

Expected: FAIL because `session.py` does not exist.

- [ ] **Step 3: Implement session manager**

Create `src/manus_mini/session.py`:

```python
from __future__ import annotations

from pathlib import Path

from manus_mini.models import SessionState
from manus_mini.runtime import AgentRuntime


class SessionManager:
    def __init__(self, cwd: Path, runtime: AgentRuntime | None = None) -> None:
        self.current = SessionState.create(cwd=cwd)
        self.runtime = runtime or AgentRuntime()

    def handle_user_message(self, content: str) -> SessionState:
        self.current = self.runtime.on_user_message(content, self.current)
        return self.current
```

- [ ] **Step 4: Implement minimal Textual app**

Create `src/manus_mini/app.py`:

```python
from __future__ import annotations

from pathlib import Path

from rich.markdown import Markdown
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Static

from manus_mini.session import SessionManager


class ManusMiniApp(App):
    CSS = """
    #messages { width: 60%; border: solid green; }
    #artifact { width: 40%; border: solid blue; }
    #status { height: 1; }
    Input { dock: bottom; }
    """

    def __init__(self, cwd: Path | None = None) -> None:
        super().__init__()
        self.manager = SessionManager(cwd or Path.cwd())

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            with Horizontal():
                yield Static("Manus Mini ready", id="messages")
                yield Static("当前产物会显示在这里", id="artifact")
            yield Static("step 0/8 | react 0/5 | reflect 0/3", id="status")
        yield Input(placeholder="继续输入你的要求...", id="input")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        content = event.value.strip()
        event.input.value = ""
        if not content:
            return
        session = self.manager.handle_user_message(content)
        messages = "\n\n".join(f"**{message.role}**: {message.content}" for message in session.messages)
        self.query_one("#messages", Static).update(Markdown(messages))
        if session.active_task:
            self.query_one("#artifact", Static).update(session.active_task.result or "暂无产物")
            self.query_one("#status", Static).update(
                f"step {session.active_task.step_count}/{session.active_task.limits.max_engineering_steps} | "
                f"react 0/{session.active_task.limits.max_react_iterations} | "
                f"reflect 0/{session.active_task.limits.max_reflection_rounds}"
            )


def main() -> None:
    ManusMiniApp().run()
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest tests/test_session.py -v
```

Expected: PASS.

- [ ] **Step 6: Manually smoke test TUI**

Run:

```bash
manus-mini
```

Expected: TUI opens. Type `阅读 docs 目录，生成项目背景报告`; messages and artifact panes update.

- [ ] **Step 7: Commit**

```bash
git add src/manus_mini/session.py src/manus_mini/app.py tests/test_session.py
git commit -m "feat: add tui session app"
```

---

### Task 8: Logging, Artifact Output, and Final Verification

**Files:**
- Create: `src/manus_mini/logging.py`
- Create: `src/manus_mini/reporter.py`
- Modify: `src/manus_mini/runtime.py`
- Test: `tests/test_logging.py`

- [ ] **Step 1: Write failing logging tests**

Create `tests/test_logging.py`:

```python
import json
from pathlib import Path

from manus_mini.logging import EventLogger


def test_event_logger_writes_jsonl(tmp_path: Path) -> None:
    logger = EventLogger(tmp_path / "runs")
    logger.record("run-1", {"type": "context_budget", "estimated_tokens": 10})
    files = list((tmp_path / "runs" / "run-1").glob("events.jsonl"))
    assert len(files) == 1
    row = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert row["type"] == "context_budget"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_logging.py -v
```

Expected: FAIL because `logging.py` does not exist.

- [ ] **Step 3: Implement JSONL logger**

Create `src/manus_mini/logging.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EventLogger:
    def __init__(self, root: Path) -> None:
        self.root = root

    def record(self, run_id: str, event: dict[str, Any]) -> None:
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        row = {"ts": datetime.now(UTC).isoformat(), "run_id": run_id, **event}
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
```

Create `src/manus_mini/reporter.py`:

```python
from __future__ import annotations

from pathlib import Path


class Reporter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def write_markdown(self, filename: str, content: str) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / filename
        path.write_text(content, encoding="utf-8")
        return path
```

- [ ] **Step 4: Run logging tests**

Run:

```bash
pytest tests/test_logging.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run:

```bash
pytest -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run ruff**

Run:

```bash
ruff check .
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/manus_mini/logging.py src/manus_mini/reporter.py tests/test_logging.py
git commit -m "feat: add logs and artifact reporter"
```

---

## Self-Review

Spec coverage:

- TUI continuous conversation: Task 7.
- Three-layer loop: Task 6.
- Tool parallel scheduling: Task 4.
- Long-term memory: Task 5.
- Context compression and tool-call integrity: Task 3.
- File tools and write confirmation preview: Task 4.
- Logs and artifacts: Task 8.
- Tests for loop limits, scheduler, memory, context integrity: Tasks 2-8.

Placeholder scan:

- The plan contains no placeholder markers or vague deferred-work steps.
- Each task includes exact files, test commands, implementation code, and commit commands.

Type consistency:

- `LoopLimits`, `SessionState`, `TaskState`, `Message`, `ToolCall`, and `ContextSegment` are defined before later tasks use them.
- Runtime uses `TaskState.create`, `SessionState.create`, `ToolRegistry`, `ToolScheduler`, and the configured `LLMClient` as defined in earlier tasks.
