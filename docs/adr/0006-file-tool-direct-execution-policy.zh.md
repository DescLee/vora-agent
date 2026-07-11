# ADR 0006：文件工具直接执行策略

## 状态

Accepted

## 背景

Vora 的 `read_file`、`write_file`、`replace_in_file` 是 Agent 主流程里最常用的文件工具。旧策略中写入工具会进入人工确认流程，虽然安全，但会打断 TUI 连续执行，也和当前用户明确要求不一致。

用户已明确要求：`read_file`、`write_file`、`replace_in_file` 直接执行，不需要用户确认，并且该策略禁止随意改回。

## 决策

`read_file`、`write_file`、`replace_in_file` 按用户要求直接执行：

- `read_file`：继续作为只读工具直接执行。
- `write_file`：`requires_confirmation = False`，不再要求 `confirmed=True`。
- `replace_in_file`：`requires_confirmation = False`，继续依赖精确文本和上下文校验保证目标明确。

直接执行不移除安全边界：

- 所有文件工具仍限制在 workspace 内。
- 受保护路径仍拒绝。
- `write_file` 覆盖已有大文件仍需要显式 `allow_full_rewrite=true`。
- `replace_in_file` 仍校验 `old_text`、`before_text`、`after_text` 和替换次数。
- 生产代码修改仍受测试证据和 Reflection gate 约束。
- `--dry-run` 只记录预览，不真实落盘。
- `write_file` / `replace_in_file` 执行前仍记录非阻塞 diff preview，供 TUI、trace 和日志审计。
- `run_bash`、`run_temp_script` 等命令工具的高风险确认策略不变。

## 后果

正向影响：

- TUI 连续执行更顺畅，文件修改不会被确认弹层频繁打断。
- 工具协议更符合用户指定的当前工作流。
- diff preview、trace 和日志仍能支撑审计与问题排查。

风险和控制：

- 文件修改会直接落盘，因此更依赖 workspace 边界、protected path、精确替换和代码修改门禁。
- 若未来要改回人工确认，必须先得到用户明确要求，并同步更新测试和文档。

## 验证

- `tests/test_tools.py` 覆盖 `write_file`、`replace_in_file` 无确认执行。
- `tests/test_runtime.py` 覆盖 ReAct 直接写入、直接替换、dry-run 不落盘、diff trace、越界拒绝和敏感 diff 脱敏。
- `tests/test_prompt_tui.py` 覆盖 `/help` 展示新的文件工具策略。
