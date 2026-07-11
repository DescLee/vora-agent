# 命令执行与测试门禁实现计划

> **给 agentic workers 的要求：** 推荐使用 `superpowers:subagent-driven-development`，也可以使用 `superpowers:executing-plans` 按任务执行。步骤使用 checkbox (`- [ ]`) 跟踪。

**目标：** 增加受控 bash / 临时脚本执行能力，并要求代码修改任务通过测试后才允许接受最终结果。

**架构：** 在现有文件工具旁新增 shell 工具，注册到 `ToolRegistry`；在 Reflection 侧增加测试门禁，从 `TaskState` 的命令观察中判断测试是否通过。临时脚本生命周期封装在工具内部，确保执行完成后删除。

**技术栈：** Python `subprocess`、`tempfile`、`pathlib`、pytest。

---

### Task 1：Shell 工具测试与实现

**文件：**
- 新建：`src/vora/tools/shell_tools.py`
- 修改：`src/vora/tools/registry.py`
- 测试：`tests/test_shell_tools.py`

- [x] 编写失败测试，覆盖 `run_bash` 成功/失败和 `run_temp_script` 清理脚本。
- [x] 运行 `pytest tests/test_shell_tools.py -q`，确认测试因功能缺失失败。
- [x] 实现 shell 工具，包含超时、输出截断、workspace cwd、exit code 元数据和脚本清理。
- [x] 注册工具。
- [x] 运行 `pytest tests/test_shell_tools.py -q`。

### Task 2：Reflection 测试门禁

**文件：**
- 修改：`src/vora/reflection.py`
- 修改：`src/vora/react.py`
- 测试：`tests/test_planner_reflector.py`

- [x] 编写失败测试，覆盖代码修改任务未执行测试不接受、测试失败不接受、测试通过才接受。
- [x] 运行目标测试，确认失败。
- [x] 在 Reflection 中增加测试门禁 helper，并把失败摘要放入后续上下文。
- [x] 更新执行阶段提示词，要求代码修改任务先准备并执行测试。
- [x] 运行目标测试。

### Task 3：回归验证

**文件：**
- 测试：现有测试套件。

- [x] 运行 `pytest -q`。
- [x] 运行 `ruff check src tests`。
- [x] 更新 `docs/fixed-issues-and-optimizations.md`，记录已完成功能和验证基线。
