# ADR 0001：自研 Agent Runtime，而不是直接封装 Agent 框架

## 状态

已采纳。

## 背景

项目目标是实现一个本地 Agent Runtime，并把 Agent 工程能力落到可解释、可测试、可运行的系统里。若直接基于 LangChain、AutoGen 或类似框架封装业务逻辑，短期可以更快得到可运行 demo，但核心运行链路、状态边界、工具治理和失败恢复会被框架隐藏。

本项目更需要显式呈现 Agent 系统的底层问题：LLM tool call 如何和工具结果配对、上下文如何压缩、写入如何确认、失败如何回流、执行过程如何被审计。这些内容也便于在面试或评审时讲清楚工程取舍。

## 决策

采用自研轻量 Agent Runtime：

- `AgentRuntime` 负责编排一轮用户输入到最终结果的工程兜底循环。
- `ReActLoop` 负责 LLM/tool call 循环。
- `ToolScheduler` 和 `Executor` 负责工具调度、确认、重试和超时。
- `ReflectionLoop` 负责质量门禁与失败回流。
- `SessionState` / `TaskState` / `TraceEvent` 用结构化状态承载全过程。

## 取舍

优势：

- 核心链路透明，讲解时可以解释每个边界的职责。
- 安全策略和上下文完整性由本项目直接控制。
- 测试可以精确注入 fake LLM、fake tool 和 fake runtime。

代价：

- 需要自己维护更多基础设施代码。
- 生态集成不如成熟框架直接。
- 多模型路由、长期记忆、评测等能力需要逐步补齐。

## 后续

如果进入生产化阶段，可以在保持当前 runtime 边界的基础上替换内部实现，例如替换 LLM adapter、引入任务队列、引入容器沙箱，而不是把业务逻辑绑定到某个 Agent 框架。
