# ADR 0003：上下文压缩必须保持 tool exchange 成组完整

## 状态

已采纳。

## 背景

OpenAI-compatible tool calling 对上下文结构有严格要求：assistant 发出的 `tool_calls` 必须和后续 tool message 按 `tool_call_id` 配对。如果压缩或裁剪上下文时只保留其中一半，会导致后续请求格式非法，或者让模型看到不完整的工具事实。

长会话 Agent 必须压缩上下文，因此压缩策略不能只按消息长度裁剪。

## 决策

将 assistant tool call 消息和对应 tool result 消息视为一个不可拆分的 `tool_exchange` 段：

- 保留时整组保留。
- 删除时整组删除，并用摘要记录关键观察结果。
- 请求 LLM 前执行 `validate_tool_call_pairs()`。
- 中断场景使用 `complete_interrupted_tool_messages()` 修复缺失的工具结果。

## 取舍

优势：

- 避免 orphan `tool_call_id` 和非法上下文。
- 让压缩结果仍可被模型理解。
- 测试可以直接覆盖完整性约束。

代价：

- 单个大型工具结果可能占用较多 token。
- 压缩器需要理解消息结构，而不是纯文本裁剪。

## 后续

后续可以把工具观察进一步结构化，例如将大文件读取结果转成 artifact 引用，只在上下文中保留摘要、路径、hash 和关键片段。
