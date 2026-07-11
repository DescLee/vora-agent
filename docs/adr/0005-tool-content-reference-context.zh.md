# ADR 0005：工具原始内容不直接常驻模型上下文

## 状态

已采纳。

## 背景

`read_file`、搜索、网页抓取和命令类工具都可能返回较大的内容。旧实现会把工具返回内容格式化成 `tool` message，再追加到 `session.messages`。这会让多文件、多轮读取持续推高上下文占比；后续只能依赖截断或压缩补救。

这个问题的本质不是单次读取上限过大，而是工具原始数据和模型上下文没有解耦。

## 决策

将工具结果分成两种上下文形态：

- 非定向整文件读取：完整原文保存在本地 `Observation.content` 和工具日志中，模型上下文只接收 `content_ref`、path、字符数和提示说明。
- 定向读取：当调用显式传入 `query`、`start_line/limit_lines` 或 `start_index/max_bytes` 时，片段内容可以进入模型上下文，但仍保留 `content_ref` 方便追踪。
- 其他长工具结果：完整内容保存在本地 `Observation.content` 和工具日志中，模型上下文只接收 `content_ref`、字符数、提示说明和头尾预览。

同时修正 `read_file` 去重 key，把 `query/start_line/limit_lines` 纳入 key，避免“整文件读过”错误阻止后续定向读取。

## 取舍

优势：

- 工具可以读取和保存完整内容，但不会默认污染后续 LLM prompt。
- 模型需要引用原文时，必须通过定向读取获取更小的上下文窗口。
- `Observation`、trace 和 JSONL 日志仍保留完整审计信息。
- 不破坏 OpenAI-compatible tool call / tool result 配对结构。
- 搜索、网页抓取、shell 输出等长结果不会在后续 ReAct 轮次中反复全量计费。

代价：

- 模型不能再依赖一次整文件读取就直接看到全文。
- Prompt 需要明确说明 `content_ref` 的含义，并引导模型继续使用 `query/start_line` 获取原文片段。
- 测试用 LLM stub 需要模拟“看到 content_ref 后定向读取”的行为。

## 验证

- Runtime 测试覆盖 `read_file` 原文不进入 `session.messages`，但仍保留在 `Observation.content`。
- Runtime 日志测试覆盖 LLM request payload 只包含 `content_ref`，工具结果日志仍保留完整内容。
- 项目概览测试覆盖先获取 content_ref，再通过定向行读取获得必要原文。
- 去重逻辑允许同一文件在整文件读取后继续按行定向读取。
- ReAct 测试覆盖通用长工具结果进入下一轮 LLM 请求前被压缩为 `content_ref`，完整内容仍保留在 `Observation.content`。
