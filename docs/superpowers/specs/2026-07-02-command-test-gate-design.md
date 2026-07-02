# Command Test Gate Design

## Goal

完善 manus-mini 的基础执行能力：支持受控 bash 执行、支持临时脚本执行后自动删除，并让代码修改任务必须通过测试脚本门禁后才允许 reflection 接受最终结果。

## Scope

- 新增受控命令工具：
  - `run_bash` 执行简短 bash 命令，工作目录固定为当前 workspace。
  - `run_temp_script` 接收脚本内容，写入系统临时目录，执行完成后删除脚本。
- 命令执行返回 `exit_code`、`stdout`、`stderr`、超时状态和摘要。
- 代码修改任务的系统提示要求先准备测试脚本或测试命令。
- Reflection 接受条件增加测试门禁：如果任务涉及代码修改，只有最近一轮测试全部通过才允许 `accept`。
- 测试失败时，失败信息进入 trace/reflection 上下文，下一轮根据失败信息继续修复。

## Non-goals

- 不实现任意 shell 长驻会话。
- 不允许命令脱离 workspace 作为默认工作目录。
- 不保留临时测试脚本文件；失败排查依赖日志和 trace 摘要。

## Safety

- `run_bash` 和 `run_temp_script` 均为 `command` 风险工具。
- 命令默认超时并截断输出，避免长时间卡死和日志爆炸。
- 临时脚本放在系统临时目录，执行后使用 `finally` 删除。

## Reflection Gate

代码修改任务通过关键词和计划意图识别。门禁判断最近的命令/脚本观察：

- 至少有一个测试类命令或临时脚本成功执行。
- 最近一次测试类执行必须 `exit_code == 0`。
- 如果最近测试失败，Reflection 返回 `regenerate`，reason 包含失败摘要。
- 如果尚未执行测试，Reflection 返回 `local_update`，要求先补测试脚本并执行。

## Validation

- 工具测试覆盖 bash 成功、失败、超时、临时脚本自动删除。
- Runtime/Reflection 测试覆盖代码修改任务未测试不接受、测试失败不接受、测试通过才接受。
