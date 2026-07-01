# Manus Mini V1 实现计划

> **给执行 Agent 的要求：** 实现本计划时必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`。按任务逐项执行，任务内使用 checkbox 追踪进度。

**目标：** 实现一个可运行的 TUI 版 Manus Mini，支持连续对话、三层 Loop Engineering、工具并行调度、长期记忆、上下文压缩、安全文件工具、运行日志和基础测试。

**架构：** 项目使用 Python 包结构，核心代码放在 `src/manus_mini`。`app.py` 负责 Textual TUI，`runtime.py` 负责外层工程兜底循环，`react.py` 负责 LLM 与工具调用之间的 ReAct 循环，`reflection.py` 负责质量评估循环，`context.py` 负责上下文预算、压缩与工具消息完整性校验，`memory.py` 负责本地长期记忆，`tools/` 负责工具协议与实现。V1 默认使用 Mock LLM，确保无网络环境也能测试和演示。

**技术栈：** Python 3.12+、Textual、Rich、Pydantic、pytest、ruff、sqlite3、asyncio。

---

## 文件结构

- 创建 `pyproject.toml`：项目元信息、依赖、console script、pytest/ruff 配置。
- 创建 `README.md`：安装方式、运行方式、演示 prompt。
- 创建 `src/manus_mini/models.py`：核心 Pydantic 模型，包括 `SessionState`、`TaskState`、`LoopLimits`、`Message`、`ToolCall`、`ContextSegment`。
- 创建 `src/manus_mini/context.py`：token 估算、上下文分段、工具调用配对校验、压缩和裁剪。
- 创建 `src/manus_mini/memory.py`：基于 sqlite3 的长期记忆管理。
- 创建 `src/manus_mini/tools/base.py`：工具协议、工具预览、工具结果模型。
- 创建 `src/manus_mini/tools/file_tools.py`：文件列举、读取、写入工具，并限制工作区路径。
- 创建 `src/manus_mini/tools/registry.py`：工具注册表。
- 创建 `src/manus_mini/scheduler.py`：工具依赖分析和并行批次调度。
- 创建 `src/manus_mini/llm.py`：Mock LLM 和结构化输出模型。
- 创建 `src/manus_mini/react.py`：ReAct 工具调用循环。
- 创建 `src/manus_mini/reflection.py`：Reflection 质量反馈循环。
- 创建 `src/manus_mini/runtime.py`：外层工程兜底循环。
- 创建 `src/manus_mini/session.py`：会话生命周期管理。
- 创建 `src/manus_mini/logging.py`：JSONL 事件日志。
- 创建 `src/manus_mini/reporter.py`：Markdown 产物输出。
- 创建 `src/manus_mini/app.py`：Textual TUI 入口。
- 创建 `tests/` 下的单元测试。

---

## 任务 1：项目骨架

**文件：**

- 创建：`pyproject.toml`
- 创建：`README.md`
- 创建：`src/manus_mini/__init__.py`
- 测试：`tests/test_package.py`

### 步骤

- [ ] **Step 1：先写失败测试**

创建 `tests/test_package.py`：

```python
from manus_mini import __version__


def test_package_version_is_defined() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest tests/test_package.py -v
```

预期：失败，因为包还没有创建。

- [ ] **Step 3：创建项目元信息**

创建 `pyproject.toml`：

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

创建 `src/manus_mini/__init__.py`：

```python
__version__ = "0.1.0"
```

创建 `README.md`：

````markdown
# Manus Mini

一个用于面试展示的 TUI 版迷你 Manus Agent。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 运行

```bash
manus-mini
```

## 演示 Prompt

- 阅读 docs 目录，生成项目背景报告
- 第二部分太泛了，补充技术架构风险
- 记住：报告用面试讲解风格
````

- [ ] **Step 4：运行测试，确认通过**

```bash
pytest tests/test_package.py -v
```

预期：通过。

- [ ] **Step 5：提交**

```bash
git add pyproject.toml README.md src/manus_mini/__init__.py tests/test_package.py
git commit -m "chore: scaffold python package"
```

---

## 任务 2：核心数据模型

**文件：**

- 创建：`src/manus_mini/models.py`
- 测试：`tests/test_models.py`

### 步骤

- [ ] **Step 1：先写失败测试**

创建 `tests/test_models.py`：

```python
from pathlib import Path

from manus_mini.models import ContextSegment, LoopLimits, Message, SessionState, TaskState


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

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest tests/test_models.py -v
```

预期：失败，因为 `models.py` 还不存在。

- [ ] **Step 3：实现核心模型**

创建 `src/manus_mini/models.py`，包含以下模型：

- `LoopLimits`：三层循环和工具重试限制。
- `Message`：对话消息，必须支持 `tool_call_ids` 与 `tool_call_id`。
- `AgentError`：Agent 错误模型。
- `Artifact`：产物模型。
- `Observation`：工具观察结果。
- `PlanStep`：计划步骤。
- `TaskState`：单轮用户请求的任务状态。
- `PendingConfirmation`：待用户确认动作。
- `CompressionSnapshot`：上下文压缩快照。
- `MemoryItem`：长期记忆条目。
- `SessionState`：会话状态。
- `ContextSegment`：上下文裁剪最小单元。
- `ContextBundle`：进入模型前的上下文包。
- `ToolCall`：工具调用描述。

实现时必须满足测试中的工厂方法：

```python
SessionState.create(cwd=tmp_path)
TaskState.create(goal="write report", cwd=tmp_path)
Message.user("hello")
Message.agent("need file", tool_call_ids=["call-1"])
Message.tool("file content", tool_call_id="call-1")
```

- [ ] **Step 4：运行测试，确认通过**

```bash
pytest tests/test_models.py -v
```

预期：通过。

- [ ] **Step 5：提交**

```bash
git add src/manus_mini/models.py tests/test_models.py
git commit -m "feat: add core state models"
```

---

## 任务 3：上下文预算与工具消息完整性

**文件：**

- 创建：`src/manus_mini/context.py`
- 测试：`tests/test_context.py`

### 步骤

- [ ] **Step 1：先写失败测试**

创建 `tests/test_context.py`：

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

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest tests/test_context.py -v
```

预期：失败，因为 `context.py` 还不存在。

- [ ] **Step 3：实现上下文工具**

创建 `src/manus_mini/context.py`，实现：

- `estimate_tokens(text, kind)`：V1 近似 token 估算。
- `validate_tool_call_pairs(messages)`：校验 `assistant/agent tool_calls` 与 `tool result` 成对。
- `build_segments(messages)`：将消息切成 `ContextSegment`，其中 `tool_exchange` 必须不可拆分。
- `ContextIntegrityError`：上下文工具消息完整性错误。

关键规则：

- 不允许出现孤儿 `tool_call_id`。
- 不允许保留 assistant 的 `tool_calls` 却丢失对应 tool result。
- 硬裁剪只能删除整个 `ContextSegment`。

- [ ] **Step 4：运行测试，确认通过**

```bash
pytest tests/test_context.py -v
```

预期：通过。

- [ ] **Step 5：提交**

```bash
git add src/manus_mini/context.py tests/test_context.py
git commit -m "feat: add context integrity checks"
```

---

## 任务 4：工具协议、文件工具与并行调度

**文件：**

- 创建：`src/manus_mini/tools/__init__.py`
- 创建：`src/manus_mini/tools/base.py`
- 创建：`src/manus_mini/tools/file_tools.py`
- 创建：`src/manus_mini/tools/registry.py`
- 创建：`src/manus_mini/scheduler.py`
- 测试：`tests/test_tools.py`
- 测试：`tests/test_scheduler.py`

### 步骤

- [ ] **Step 1：先写失败测试**

测试必须覆盖：

- 读取文件不能逃逸工作区。
- `list_files` 返回相对路径。
- `write_file` 的 preview 必须要求用户确认。
- 无依赖只读工具可以进入同一个并行批次。
- 有 `depends_on` 的工具必须拆成串行批次。

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest tests/test_tools.py tests/test_scheduler.py -v
```

预期：失败，因为工具模块还不存在。

- [ ] **Step 3：实现工具基础协议**

创建 `src/manus_mini/tools/base.py`，包含：

- `ToolPreview`
- `ToolResult`
- `Tool` Protocol

要求：

- `ToolPreview.requires_confirmation` 默认是 `False`。
- 写入类工具必须返回 `requires_confirmation=True`。
- `ToolResult` 必须包含 `ok`、`summary`、`content`、`data`、`error_code`。

- [ ] **Step 4：实现文件工具**

创建 `src/manus_mini/tools/file_tools.py`，包含：

- `resolve_workspace_path(cwd, value)`：禁止路径逃逸工作区。
- `ListFilesTool`
- `ReadFileTool`
- `WriteFileTool`

要求：

- 所有路径必须 resolve 后校验。
- `ReadFileTool` 找不到文件时返回 `FILE_NOT_FOUND`。
- `WriteFileTool.preview()` 必须要求确认。

- [ ] **Step 5：实现工具注册表**

创建 `src/manus_mini/tools/registry.py`：

- 注册 `list_files`
- 注册 `read_file`
- 注册 `write_file`
- 提供 `get(name)` 和 `names()`

- [ ] **Step 6：实现工具调度器**

创建 `src/manus_mini/scheduler.py`：

- 输入 `list[ToolCall]`
- 输出 `list[list[ToolCall]]`
- 无依赖、无资源冲突、`risk_level=safe` 的工具进入同一批次。
- 有依赖、写入类、命令类或资源冲突的工具串行。
- 发现循环依赖时抛出 `ValueError("Tool dependency cycle detected")`。

- [ ] **Step 7：运行测试，确认通过**

```bash
pytest tests/test_tools.py tests/test_scheduler.py -v
```

预期：通过。

- [ ] **Step 8：提交**

```bash
git add src/manus_mini/models.py src/manus_mini/tools src/manus_mini/scheduler.py tests/test_tools.py tests/test_scheduler.py
git commit -m "feat: add file tools and scheduler"
```

---

## 任务 5：长期记忆管理

**文件：**

- 创建：`src/manus_mini/memory.py`
- 测试：`tests/test_memory.py`

### 步骤

- [ ] **Step 1：先写失败测试**

测试必须覆盖：

- 可以保存用户偏好。
- 可以按关键词检索记忆。
- 包含密钥、token、password、secret 的内容不会写入长期记忆。

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest tests/test_memory.py -v
```

预期：失败，因为 `memory.py` 还不存在。

- [ ] **Step 3：实现 sqlite 记忆管理器**

创建 `src/manus_mini/memory.py`，实现：

- `MemoryManager(db_path)`
- `add(scope, kind, content, tags)`
- `add_if_allowed(scope, kind, content, tags)`
- `search(query, limit=5)`

要求：

- 使用标准库 `sqlite3`。
- 记忆表包含 `id`、`scope`、`kind`、`content`、`tags`、`confidence`、`created_at`、`updated_at`。
- `add_if_allowed` 必须过滤敏感内容。

- [ ] **Step 4：运行测试，确认通过**

```bash
pytest tests/test_memory.py -v
```

预期：通过。

- [ ] **Step 5：提交**

```bash
git add src/manus_mini/memory.py tests/test_memory.py
git commit -m "feat: add local memory manager"
```

---

## 任务 6：三层 Runtime 与 Mock LLM

**文件：**

- 创建：`src/manus_mini/llm.py`
- 创建：`src/manus_mini/react.py`
- 创建：`src/manus_mini/reflection.py`
- 创建：`src/manus_mini/runtime.py`
- 测试：`tests/test_runtime.py`

### 步骤

- [ ] **Step 1：先写失败测试**

测试必须覆盖：

- Runtime 能生成报告回复。
- Runtime 遵守外层 `max_engineering_steps`。
- 工具调用结果会进入 `TaskState.observations`。

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest tests/test_runtime.py -v
```

预期：失败，因为 runtime 相关模块还不存在。

- [ ] **Step 3：实现 Mock LLM**

创建 `src/manus_mini/llm.py`：

- `LLMResult`
- `MockLLMClient.complete_with_tools(messages, tool_names)`

行为：

- 用户要求读取 `a.md` 时返回 `read_file` tool call。
- 用户提到 `docs` 时返回 `list_files` tool call。
- 没有工具需求时返回报告草稿。

- [ ] **Step 4：实现 ReAct Loop**

创建 `src/manus_mini/react.py`：

- 每轮调用 Mock LLM。
- 如果有 `tool_calls`，先通过 `ToolScheduler.plan_batches()` 分批。
- 执行工具后生成 `Observation`。
- 达到 `max_react_iterations` 时抛出 `MAX_REACT_ITERATIONS_REACHED` 类错误。

- [ ] **Step 5：实现 Reflection Loop**

创建 `src/manus_mini/reflection.py`：

- 调用 ReAct Loop 得到草稿。
- 草稿非空则接受。
- 达到 `max_reflection_rounds` 后保留当前最佳结果，并标记原因。

- [ ] **Step 6：实现 Runtime**

创建 `src/manus_mini/runtime.py`：

- `AgentRuntime.on_user_message(content, session)`
- 追加用户消息。
- 创建 `TaskState`。
- 执行外层工程兜底循环。
- 追加 Agent 回复。
- 更新 `session.active_task`。

- [ ] **Step 7：运行测试，确认通过**

```bash
pytest tests/test_runtime.py -v
```

预期：通过。

- [ ] **Step 8：提交**

```bash
git add src/manus_mini/llm.py src/manus_mini/react.py src/manus_mini/reflection.py src/manus_mini/runtime.py tests/test_runtime.py
git commit -m "feat: add three layer runtime"
```

---

## 任务 7：TUI 应用与会话生命周期

**文件：**

- 创建：`src/manus_mini/session.py`
- 创建：`src/manus_mini/app.py`
- 测试：`tests/test_session.py`

### 步骤

- [ ] **Step 1：先写失败测试**

测试必须覆盖：

- `SessionManager` 可以创建空会话。
- `SessionManager.handle_user_message()` 能追加用户消息并生成 Agent 回复。

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest tests/test_session.py -v
```

预期：失败，因为 `session.py` 不存在。

- [ ] **Step 3：实现 SessionManager**

创建 `src/manus_mini/session.py`：

- `SessionManager(cwd, runtime=None)`
- `current: SessionState`
- `handle_user_message(content) -> SessionState`

- [ ] **Step 4：实现最小 TUI**

创建 `src/manus_mini/app.py`：

- 使用 Textual。
- 页面包含 Header、对话区、当前产物区、状态栏、输入框、Footer。
- 输入提交后调用 `SessionManager.handle_user_message()`。
- 状态栏展示 `step x/8 | react 0/5 | reflect 0/3`。

- [ ] **Step 5：运行测试，确认通过**

```bash
pytest tests/test_session.py -v
```

预期：通过。

- [ ] **Step 6：手动冒烟测试**

```bash
manus-mini
```

预期：TUI 打开。输入 `阅读 docs 目录，生成项目背景报告` 后，对话区和产物区会更新。

- [ ] **Step 7：提交**

```bash
git add src/manus_mini/session.py src/manus_mini/app.py tests/test_session.py
git commit -m "feat: add tui session app"
```

---

## 任务 8：日志、产物输出与最终验证

**文件：**

- 创建：`src/manus_mini/logging.py`
- 创建：`src/manus_mini/reporter.py`
- 修改：`src/manus_mini/runtime.py`
- 测试：`tests/test_logging.py`

### 步骤

- [ ] **Step 1：先写失败测试**

测试必须覆盖：

- `EventLogger` 能写入 `runs/<run_id>/events.jsonl`。
- 写入内容是 JSONL。
- 日志里包含 `run_id`、事件类型和时间戳。

- [ ] **Step 2：运行测试，确认失败**

```bash
pytest tests/test_logging.py -v
```

预期：失败，因为 `logging.py` 还不存在。

- [ ] **Step 3：实现 JSONL 日志**

创建 `src/manus_mini/logging.py`：

- `EventLogger(root)`
- `record(run_id, event)`

要求：

- 自动创建 `runs/<run_id>/`。
- 每行写入一个 JSON 对象。
- 使用 `ensure_ascii=False` 保留中文。

- [ ] **Step 4：实现产物输出**

创建 `src/manus_mini/reporter.py`：

- `Reporter(output_dir)`
- `write_markdown(filename, content) -> Path`

要求：

- 自动创建 `outputs/`。
- 使用 UTF-8 写入。

- [ ] **Step 5：运行日志测试**

```bash
pytest tests/test_logging.py -v
```

预期：通过。

- [ ] **Step 6：运行完整测试**

```bash
pytest -v
```

预期：全部通过。

- [ ] **Step 7：运行 ruff**

```bash
ruff check .
```

预期：通过。

- [ ] **Step 8：提交**

```bash
git add src/manus_mini/logging.py src/manus_mini/reporter.py tests/test_logging.py
git commit -m "feat: add logs and artifact reporter"
```

---

## 自检

规格覆盖：

- TUI 连续对话：任务 7。
- 三层 Loop：任务 6。
- 工具并行调度：任务 4。
- 长期记忆：任务 5。
- 上下文压缩和工具消息完整性：任务 3。
- 文件工具与写入确认预览：任务 4。
- 日志和产物：任务 8。
- 循环限制、调度器、记忆、上下文完整性测试：任务 2-8。

占位符检查：

- 本计划不包含占位符标记或模糊的延期实现描述。
- 每个任务都包含明确文件、测试命令、实现要求和提交命令。

类型一致性：

- `LoopLimits`、`SessionState`、`TaskState`、`Message`、`ToolCall`、`ContextSegment` 在后续任务使用前已经定义。
- Runtime 使用的 `TaskState.create`、`SessionState.create`、`ToolRegistry`、`ToolScheduler`、`MockLLMClient` 都在前置任务中定义。
