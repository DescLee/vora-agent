# ADR 0004：代码类任务在 Reflection 阶段执行 pytest 验收门禁

## 状态

已采纳。

## 背景

Agent 修改代码后，如果只让模型自评“已经完成”，很容易出现未测试、测试失败仍宣称完成、或修改没有覆盖用户原始目标的问题。高级工程岗位面试中，代码修改后的质量门禁是区分 demo 和工程系统的重要信号。

非代码任务的验收标准更偏语义，目前不适合直接生成 pytest 文件执行。第一版先对代码类任务启用真实 pytest gate，非代码任务暂时直接放过。

## 决策

在 `ReflectionLoop.run()` 中区分任务类型：

- 非代码任务：直接接受，并标记为 `non-code task accepted without pytest reflection gate`。
- 代码类任务：生成临时 `test_reflection_acceptance.py`，执行 `python -m pytest`。
- pytest gate 检查草稿非空，并确认最新代码变更后存在通过的测试证据。
- gate 失败时返回 `local_update`，reason 中包含原始输入、pytest case 内容、case 路径和 pytest 输出。
- Runtime 收到未 accept 的 Reflection 结果后，将 reason 追加为 system message，让下一轮 ReAct/Executor 继续处理。

## 取舍

优势：

- 让代码任务从“模型自评”升级为“工程门禁”。
- 失败信息可以直接回流给下一轮执行。
- 临时 pytest 文件不会污染项目工作区。

代价：

- 当前 pytest case 主要验证执行链路和测试证据，不理解业务语义。
- 如果项目没有 pytest 环境，gate 会失败并要求继续补测试或调整验证方式。
- 非代码任务的结构化验收留到后续版本。

## 后续

后续可扩展为两层验收：

1. 代码任务：由模型根据原始输入生成更贴近业务语义的 pytest case，并在沙箱中运行。
2. 非代码任务：使用结构化 acceptance case，例如必须包含事实来源、不得出现空泛结论、必须回应用户约束。
