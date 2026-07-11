# Reflection Pytest Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让代码类任务在 Reflection 阶段生成并执行真实 pytest 验收文件，失败时把原始输入、case 和失败原因回流给下一轮执行。

**Architecture:** 非代码任务继续直接接受，代码类任务在 `ReflectionLoop.run()` 中执行 `_decide()`。当任务涉及代码修改时，Reflection 生成一个临时 pytest 文件并运行；pytest 失败或没有测试通过证据时返回 `local_update`，由 Runtime 追加 system message 让 ReAct/Executor 继续。

**Tech Stack:** Python 3.12、pytest、现有 `ReflectionLoop`、`TaskState.trace_events`、`subprocess` 临时文件执行。

---

### Task 1: 接入 Reflection 决策

**Files:**
- Modify: `tests/test_planner_reflector.py`
- Modify: `src/vora/reflection.py`

- [ ] **Step 1: Write failing test**

新增测试：代码修改任务如果没有测试证据，`ReflectionLoop.run()` 不能 forced accept。

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_planner_reflector.py::test_reflection_run_rejects_code_task_without_validation -q`
Expected: FAIL because current `run()` returns `reflection forced accept`.

- [ ] **Step 3: Implement minimal code**

让 `run()` 调用 `_decide()`，并按 decision 返回 `accepted`。

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_planner_reflector.py::test_reflection_run_rejects_code_task_without_validation -q`
Expected: PASS.

### Task 2: 生成并执行 pytest case

**Files:**
- Modify: `tests/test_planner_reflector.py`
- Modify: `src/vora/reflection.py`

- [ ] **Step 1: Write failing tests**

新增测试：代码任务会生成 pytest case 并记录执行结果；pytest 失败时 reason 包含原始输入和 case 路径。

- [ ] **Step 2: Verify RED**

Run targeted pytest tests and confirm failure.

- [ ] **Step 3: Implement minimal code**

在 Reflection 中创建临时 pytest 文件，case 检查草稿非空、存在最近写入后的测试通过证据；执行 `python -m pytest`，将 stdout/stderr 和 case 内容写入 trace。

- [ ] **Step 4: Verify GREEN and full suite**

Run: `pytest tests/test_planner_reflector.py -q` then `pytest -q`.

