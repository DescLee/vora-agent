# Manus Mini 项目讲解 Demo 场景

## 目标

项目讲解不要临场随机输入。建议准备固定场景，分别展示架构理解、工具治理、安全确认和质量门禁。

## Demo 0：快速自检

先运行：

```bash
ruff check src tests evals
pytest -q
python evals/run_evals.py
```

展示点：

- 传统单测覆盖模块行为。
- eval 覆盖 Agent 产品级约束。
- Reflection、memory、context、安全边界都有可验证用例。

## Demo 1：项目分析

输入：

```text
请分析当前项目的技术深度，重点说架构、测试、安全和短板。
```

预期展示：

- Agent 读取项目结构和关键文件。
- TUI 展示工具调用过程。
- 最终输出结构化结论。

讲解重点：

- `Planner` 将目标拆成 research/report 步骤。
- `ReActLoop` 决定读取哪些文件。
- `ToolScheduler` 对只读工具做批次调度。
- `Reporter` 输出 Markdown 产物。

## Demo 2：写入确认

建议在临时分支或临时目录中演示。

输入：

```text
请在 docs 下新增一份简短的项目亮点说明，适合项目讲解。
```

预期展示：

- Agent 准备写文件。
- TUI 弹出确认面板。
- 展示 diff preview。
- 用户确认后才执行写入。

讲解重点：

- 模型不能直接写文件，执行层会二次确认。
- `PendingConfirmation` 将待确认动作纳入会话状态。
- 用户拒绝不视为系统错误，而是作为 observation 回流。

## Demo 3：代码任务 Reflection pytest gate

输入：

```text
请修改一个小函数，并补充或运行测试验证。
```

预期展示：

- 如果 Agent 修改代码但没有测试证据，Reflection 会拒绝 accept。
- Reflection 生成临时 pytest case。
- 失败 reason 包含原始输入、case 内容和 pytest 输出。
- Runtime 将失败原因作为 system message 回流，下一轮继续执行。

讲解重点：

- 这不是模型自评，而是执行真实 pytest 的工程门禁。
- 当前 gate 验证“最新代码变更后有通过测试证据”。
- 后续可以扩展为由模型根据原始需求生成业务级 pytest。

## Demo 4：安全边界

可以直接运行 eval：

```bash
python evals/run_evals.py
```

重点看：

- `path_escape_is_rejected`
- `sensitive_memory_is_rejected`
- `tool_exchange_integrity_is_enforced`

讲解重点：

- 路径限制在工具执行层，不依赖模型遵守。
- 敏感信息不进入长期记忆。
- tool call 和 tool result 成组完整，避免上下文损坏。

## 常见追问回答

| 问题 | 回答重点 |
|---|---|
| 为什么不用 LangChain？ | 本项目目标是展示底层 Agent Runtime 能力，核心链路需要可控、可测、可解释。 |
| 生产化还差什么？ | 任务队列、容器沙箱、幂等恢复、多租户权限、观测指标、多 provider fallback。 |
| Reflection gate 是否理解业务？ | 当前 gate 先保证代码任务必须有测试证据；下一版会生成更贴近原始需求的业务级 pytest。 |
| 安全如何保证？ | 工具执行层二次校验，写入和命令 human-in-the-loop，生产环境会迁移到强沙箱。 |
