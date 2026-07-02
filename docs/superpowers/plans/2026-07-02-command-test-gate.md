# Command Test Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add controlled bash/script execution and require passing tests before accepting code-modification tasks.

**Architecture:** Add shell tools beside existing file tools, register them in `ToolRegistry`, and add a reflection-side test gate that reads command observations from `TaskState`. Keep script lifecycle inside the tool so temporary files are removed in `finally`.

**Tech Stack:** Python `subprocess`, `tempfile`, `pathlib`, pytest.

---

### Task 1: Shell Tool Tests And Implementation

**Files:**
- Create: `src/manus_mini/tools/shell_tools.py`
- Modify: `src/manus_mini/tools/registry.py`
- Test: `tests/test_shell_tools.py`

- [ ] Write failing tests for `run_bash` success/failure and `run_temp_script` cleanup.
- [ ] Run `pytest tests/test_shell_tools.py -q` and confirm failures.
- [ ] Implement shell tools with timeout, output truncation, workspace cwd, exit code metadata, and script cleanup.
- [ ] Register the tools.
- [ ] Run `pytest tests/test_shell_tools.py -q`.

### Task 2: Reflection Test Gate

**Files:**
- Modify: `src/manus_mini/reflection.py`
- Modify: `src/manus_mini/react.py`
- Test: `tests/test_reflection.py` or existing reflection tests.

- [ ] Write failing tests for code modification tasks: no test run rejects accept, failed test rejects accept, passing test allows accept.
- [ ] Run targeted tests and confirm failures.
- [ ] Add test-gate helpers in reflection and include failure summaries in follow-up context.
- [ ] Update execution prompt to require tests at the start of code-modification work.
- [ ] Run targeted tests.

### Task 3: Regression Sweep

**Files:**
- Test: existing suite.

- [ ] Run `pytest -q`.
- [ ] Run `ruff check src tests`.
- [ ] Update `docs/fixed-issues-and-optimizations.md` with the completed feature and verification baseline.
