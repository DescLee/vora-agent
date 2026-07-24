# 项目检索效率优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 减少项目问答中的重复扫描和读取轮次，并提供通用的候选文件索引。

**Architecture:** 保留现有 ReAct 和 ToolRegistry，修正 `read_file` 上下文回填策略，增加结构化代码搜索工具，并通过独立项目索引模块向 Planner/ReAct 注入轻量候选信息。所有新增能力使用 Python 标准库和现有缓存目录。

**Tech Stack:** Python 3.12、Pydantic、pytest、现有 Vora ToolRegistry 与项目缓存。

---

### Task 1: 小文件正文内联

**Files:**
- Modify: `src/vora/react.py`
- Test: `tests/test_runtime.py`

- [ ] 新增失败测试：普通读取小文件时工具消息包含正文，普通读取大文件时仍返回 `content_ref`。
- [ ] 运行定向测试确认小文件用例失败。
- [ ] 调整 `format_tool_result_message` 和 `model_content_inlined` 元数据判断。
- [ ] 运行相关 runtime 测试确认通过。

### Task 2: 开放 glob 和增加 search_code

**Files:**
- Modify: `src/vora/react.py`
- Modify: `src/vora/tools/file_tools.py`
- Modify: `src/vora/tools/registry.py`
- Modify: `src/vora/tools/__init__.py`
- Test: `tests/test_tools.py`
- Test: `tests/test_runtime.py`

- [ ] 新增失败测试：各类项目只读任务包含 `glob` 和 `search_code`。
- [ ] 新增失败测试：`search_code` 返回路径、行号、片段并遵守上限及忽略规则。
- [ ] 注册 `SearchCodeTool` 并加入对应工具白名单。
- [ ] 运行工具和 runtime 定向测试确认通过。

### Task 3: 通用增量项目索引

**Files:**
- Create: `src/vora/project_index.py`
- Modify: `src/vora/planner.py`
- Modify: `src/vora/react.py`
- Test: `tests/test_project_index.py`
- Test: `tests/test_planner_reflector.py`

- [ ] 新增失败测试：索引识别 manifest、入口、测试和声明符号。
- [ ] 新增失败测试：文件修改后缓存失效并更新。
- [ ] 实现受限扫描、签名缓存和文本格式化。
- [ ] 将精简索引注入 Planner 与项目相关 ReAct 提示词。
- [ ] 运行索引和提示词定向测试确认通过。

### Task 4: 回归验证

**Files:**
- Modify: `README.md`
- Modify: `docs/v1-technical-design.md`

- [ ] 同步更新中文项目文档中的检索策略说明。
- [ ] 运行 `pytest -q`。
- [ ] 运行 `ruff check src tests`。
- [ ] 检查 git diff，确认没有无关改动。
