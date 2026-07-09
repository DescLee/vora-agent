# Manus Mini 测试与评测策略

## 目标

Agent 系统不能只验证函数正确，还要验证任务行为、安全边界和失败回流。本项目采用三层质量体系：

1. 单元测试：验证模块行为。
2. 集成测试：验证 Runtime/ReAct/Reflection/Tool 链路。
3. Eval：验证 Agent 关键产品约束。

## 单元测试

覆盖重点：

| 模块 | 测试目标 |
|---|---|
| `models.py` | 状态模型、消息结构、tool call id 关系。 |
| `context.py` | tool exchange 完整性、压缩、上下文预算。 |
| `tools/*` | 文件读写、路径限制、写入确认、命令风险。 |
| `scheduler.py` | 并行批次、依赖关系、敏感工具串行。 |
| `memory.py` | SQLite 存储、检索、删除、敏感信息过滤。 |
| `llm.py` | OpenAI-compatible payload、tool call 解析、错误包装。 |
| `prompt_tui*.py` | TUI 文本渲染、确认面板、滚动和状态展示。 |

## 集成测试

集成测试不依赖真实 LLM，而是注入 fake LLM / fake ReAct / fake tool：

- Runtime 能从用户输入生成任务、计划、工具调用和结果。
- ReAct 能处理多轮 tool calls。
- Reflection 能拒绝未测试的代码任务。
- 写入确认能暂停任务并在确认后继续。
- 会话恢复能修复中断 tool message。

这种 harness 设计让测试稳定、可复现，并避免网络依赖。

## Eval

`evals/run_evals.py` 覆盖的是产品级约束：

- 代码任务没有测试证据时不能通过 Reflection。
- 代码任务有测试证据时会执行 pytest gate。
- 非代码任务当前版本不运行 pytest gate。
- 敏感信息不能写入 memory。
- tool exchange 必须成组完整。
- 只读工具可以进入同一并行批次。
- 文件工具拒绝 workspace 外路径。

运行方式：

```bash
python evals/run_evals.py
```

## CI 建议

本地和 CI 推荐顺序：

```bash
ruff check src tests evals
pytest -q
python evals/run_evals.py
```

如果时间有限，PR 快速检查可以先跑：

```bash
pytest tests/test_runtime.py tests/test_planner_reflector.py -q
python evals/run_evals.py
```

## 当前边界

- Eval 目前是规则化 harness，不依赖真实模型输出。
- 非代码任务还没有结构化验收 case。
- 没有固定真实仓库任务集和人工评分集。
- 没有统计长期任务完成率、工具调用成功率和人工确认率。

## 后续演进

下一步建议补：

1. `evals/cases.zh.json`：声明式任务集。
2. eval report：输出成功率、失败原因、工具调用链。
3. 真实 demo 任务：项目分析、写入确认、代码修复后测试。
4. 安全 eval：越权路径、危险命令、敏感信息泄露。
5. 模型回归 eval：同一任务在不同模型上的完成率和成本。
