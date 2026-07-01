# Manus Mini V1 完成度测试清单

生成日期：2026-07-01

本文用于对照 `docs/v1-product-design.md` 和 `docs/v1-technical-design.md`，审查 Manus Mini V1 当前实现与第一版功能/技术承诺之间的差距。

状态说明：

- 已完成：代码已实现，且已有基础测试或可直接验证。
- 部分完成：已有骨架或局部能力，但未达到文档描述的完整行为。
- 未完成：文档要求存在，但当前代码未实现或未接入主链路。
- 未核验：需要端到端演示或人工操作进一步确认。

当前验证结果：

```text
pytest: 121 passed in 2.65s
```

注意：测试通过只说明当前已有行为稳定，不代表 V1 文档承诺全部完成。

## 总体结论

当前项目已经完成了 TUI Agent 的核心骨架：TUI 入口、会话状态、Runtime/ReAct/Reflection 基础链路、文件工具、工具调度、上下文裁剪基础、长期记忆存储、日志与 Markdown 产物输出。

但按 V1 文档验收标准看，当前还不是完整 V1。主要缺口集中在：真实写入确认、完整 Reflection 决策、Planner/Executor/Observer/Reflector 拆分与接入、三类工具包、CLI 参数、dry-run、长期记忆注入、CompressionSnapshot、端到端 demo。

## 测试清单

| 测试项目 | 完成情况 | 结论 |
|---|---:|---|
| TUI 入口与连续对话 | 部分完成 | 有 `manus-mini` 和 prompt_toolkit TUI，能输入、渲染消息和产物；但 CLI 参数未实现，入口只是 `PromptTui().run()`。 |
| 四区布局：消息区/产物区/状态区/输入区 | 部分完成 | 已有基础展示；但工具确认、context/memory 状态展示不完整。 |
| CLI 参数：`--cwd`、`--max-steps`、`--max-react`、`--max-reflect`、`--dry-run` 等 | 未完成 | 产品文档列了参数，但代码未见 argparse/click 或等价解析逻辑。 |
| 三层 Loop 基础结构 | 部分完成 | Runtime -> Reflection -> ReAct 已接入；但 Reflection 和工程兜底策略不完整。 |
| ReAct 工具循环 | 已完成 | 能处理 LLM tool_calls、执行工具、回填工具结果、达到上限报错。 |
| Reflection 质量反馈循环 | 部分完成 | 有循环壳；但只要草稿非空就接受，没有 `accept/local_update/regenerate/replan` 决策。 |
| 工程兜底循环 | 部分完成 | 有步数、运行时长、异常兜底；但当前每轮后直接 `break`，没有真正多工程步推进。 |
| Planner | 未完成 | 文档要求 Planner；代码中无独立 Planner，只有基于用户输入创建的简单 `PlanStep`。 |
| Executor | 部分完成 | 工具执行逻辑存在，但内聚在 `ReActLoop` 内，没有独立 Executor 模块。 |
| Observer | 部分完成 | `Observation` 模型和生成逻辑存在，但没有独立 Observer，也没有系统化错误到观察的转换策略。 |
| Reflector | 未完成 | 文档要求 Reflector 评判结果质量并给出跳转决策；当前没有独立 Reflector。 |
| ToolScheduler 并行调度 | 部分完成 | 调度器和测试存在；但日志没有完整记录 `batch_id`、耗时、parallel 等批次信息。 |
| 文件工具：`list_files`、`read_file`、`write_file` | 已完成 | 三个基础文件工具已实现并有测试。 |
| 文件工具：`append_file`、`make_directory` | 未完成 | 技术文档列入 V1 File Tools，但当前未实现。 |
| Research Pack | 未完成 | `collect_local_docs`、`summarize_text`、`generate_markdown_report` 未实现。 |
| Code Pack | 未完成 | `scan_project`、`read_code_file`、`propose_patch`、`apply_text_edit` 未实现。 |
| Automation Pack | 未完成 | `extract_todos`、`organize_notes`、`generate_checklist` 未实现。 |
| 文件写入确认 UI | 未完成 | `WriteFileTool` 支持未确认时拒绝写入，但 ReAct 路径会自动补 `confirmed=True`，没有真正让用户确认。 |
| 用户拒绝写入后的替代流程 | 未完成 | 文档要求拒绝写入作为正常观察交给 Agent 继续反思；当前没有应用层确认/拒绝流程。 |
| `dry-run` 模式 | 未完成 | 文档要求 dry-run 不真实写入；当前没有模式参数和对应策略。 |
| 路径逃逸限制 | 已完成 | `resolve_workspace_path()` 限制 workspace 内路径，测试覆盖。 |
| 默认不执行 shell 命令 | 已完成 | 当前没有 command 工具。 |
| 写入前保存原内容摘要 | 未完成 | 文档要求便于后续回滚扩展；当前写入工具未保存原内容摘要。 |
| 长期记忆存储 | 部分完成 | `MemoryManager` 可 add/search/filter；但未接入 Runtime/TUI 自动提取、注入和删除流程。 |
| 记忆注入到模型上下文 | 未完成 | 文档要求每轮最多注入相关记忆；当前主链路未接入 MemoryManager 检索。 |
| 记忆删除：“忘记偏好” | 未完成 | 文档要求用户触发删除并确认；当前未见流程。 |
| 上下文压缩基础 | 部分完成 | `compact_messages()` 可按预算压缩并保持 tool_call 配对；但未生成 `CompressionSnapshot`，未接入 session 状态和 TUI 提示。 |
| ContextBundle 构建 | 部分完成 | 模型存在，实际 ReAct 只使用 `compact_messages()`，没有完整 bundle 组装。 |
| token 预算触发策略 | 部分完成 | 有近似 token 估算和预算裁剪；没有 70%/90% 触发策略和预算分配策略。 |
| token 预算日志 | 未完成 | 文档要求 `context_budget` 日志；当前未见。 |
| 压缩摘要进入 TUI | 未完成 | 文档要求对话区追加压缩系统消息；当前未接入。 |
| 执行日志 `events.jsonl` | 部分完成 | 有 JSONL 事件；但不完整记录计划、反思结论、工具耗时、批次编号。 |
| `runs/<run_id>/summary.md` | 未完成 | 文档要求 runs 下有 summary；当前 Reporter 写最终 Markdown 到 `outputs/`。 |
| Markdown 产物输出 | 已完成 | 每轮会写 `outputs/<timestamp>-<run_id>.md`。 |
| 多轮基于当前产物修改 | 部分完成 | Session 保留消息历史；但没有产物版本摘要、局部修改 Planner、当前产物内容注入策略。 |
| 三类任务 demo | 未完成 | 文档要求资料调研、本地项目助手、任务自动化各有一个 demo；当前无明确 demo 脚本/测试。 |
| 连续两轮修改上一轮产物 demo | 未完成 | 当前没有端到端 demo 证明。 |
| 三层最大循环次数展示 demo | 部分完成 | UI 能展示上限；但 reflect/react 当前轮次并未完整实时体现。 |
| 记住用户偏好并在后续回复使用 demo | 未完成 | MemoryManager 未接入主链路。 |
| 长会话压缩 demo | 未完成 | 有单元测试，但没有端到端 demo 展示压缩摘要。 |
| 写入确认 demo | 未完成 | 工具层有确认测试，应用层没有用户确认流程。 |
| Runtime 最大步数内正常结束 | 已完成 | 有测试覆盖。 |
| Runtime 达到最大步数时输出部分结果 | 部分完成 | 有步数测试；但“部分结果、未完成原因和下一步建议”不完整。 |
| ReAct 达到最大工具循环次数时抛出可重试错误 | 已完成 | 有测试覆盖。 |
| 并行批次中单个工具失败处理 | 部分完成 | 工具失败不会直接丢失观察；但工程兜底降级策略不完整。 |
| Reflection 四分支测试 | 未完成 | 文档要求 `accept/local_update/regenerate/replan` 分支；当前没有这些分支。 |
| 工程兜底处理工具超时 | 未完成 | 没有工具超时机制。 |
| 工程兜底处理非法 LLM 输出 | 部分完成 | OpenAI-compatible 解析异常会转 LLM 错误；没有结构化重试/fallback 策略。 |
| 工程兜底处理 token 超限 | 未完成 | 没有 `TOKEN_BUDGET_EXCEEDED` 主链路处理。 |
| 工具重试耗尽 | 部分完成 | 单工具失败会重试并标记 `TOOL_RETRY_EXHAUSTED`；但交给 Reflection 替代路径不完整。 |
| TUI 输入消息后追加用户消息、触发任务并渲染 Agent 回复 | 已完成 | 有基础测试覆盖。 |
| 敏感内容不写入长期记忆 | 已完成基础 | `MemoryManager.add_if_allowed()` 有过滤测试。 |
| 只读工具不能写文件 | 已完成 | 只读工具实现和测试覆盖。 |
| 写入工具未确认不修改文件 | 已完成于工具层 | 工具层拒绝未确认写入；应用层目前绕过确认。 |
| ToolResult 错误转 Observation | 部分完成 | ReAct 会构造 Observation；但无独立 Observer。 |
| Reporter 生成 Markdown 摘要 | 已完成 | 有 Reporter 和测试。 |

## 高优先级缺口

1. 写入确认链路需要优先修正：不能由 ReAct 自动补 `confirmed=True` 绕过用户确认。
2. Reflection 需要从“非空即接受”升级为真正的质量反馈：至少支持 `accept/local_update/regenerate/replan`。
3. CLI 参数和 `dry-run` 应补齐，否则文档中的运行方式不成立。
4. 长期记忆和上下文压缩需要接入主链路，否则只是可测试组件，不是产品能力。
5. 三类工具包和 demo 需要补齐，否则 V1 验收标准中的“三类任务各有 demo”无法通过。

## 建议验收顺序

1. 权限与确认：写入确认、拒绝写入、dry-run、路径限制。
2. Loop 完整性：Runtime 多工程步、Reflection 四分支、ReAct 上限交给 Reflection。
3. 记忆与压缩：偏好保存/检索/注入、CompressionSnapshot、TUI 压缩提示。
4. 工具包：File Tools 补齐，再做 Research/Code/Automation 最小集。
5. Demo 与日志：三类任务 demo、两轮产物修改、批次日志、runs summary。
