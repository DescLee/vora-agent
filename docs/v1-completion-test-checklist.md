# Manus Mini V1 完成度测试清单

生成日期：2026-07-01

本文用于对照 `docs/v1-product-design.md` 和 `docs/v1-technical-design.md`，审查 Manus Mini V1 当前实现与第一版功能/技术承诺之间的差距。

状态说明：

- 已完成：代码已实现，且已有基础测试或可直接验证。
- 部分完成：已有骨架或局部能力，但未达到文档描述的完整行为。
- 未完成：文档要求存在，但当前代码未实现或未接入主链路。
- 未核验：需要端到端演示或人工操作进一步确认。

优先级说明：

- P0：最严重，影响主流程、安全边界或核心 Agent 闭环成立。
- P1：V1 核心验收能力，影响产品完整性或关键演示，但不一定阻断最小运行。
- P2：影响可观察性、演示可信度、体验完整性或扩展性。
- P3：低风险补齐、已完成项验证、文档一致性或后续优化。

当前验证结果：

```text
pytest: 327 passed in 10.50s
ruff check src tests evals: passed
python evals/run_evals.py: 7 passed
```

注意：测试通过只说明当前已有行为稳定，不代表 V1 文档承诺全部完成。

## 总体结论

当前项目已经补齐了 TUI Agent 的主链路：TUI 入口、会话状态、Runtime/ReAct/Reflection、Planner/Executor/Observer/Reflector、文件工具、命令工具、工具调度、上下文压缩、长期记忆、确认写入、dry-run、日志与 Markdown 产物输出。

代码类任务的 Reflection 已经从 forced accept 升级为 pytest 验收门禁：没有测试证据时不允许通过，会把原始输入、pytest case 和失败原因回流到下一轮执行。非代码任务当前版本仍直接放过，下一版本再接结构化验收 case。

当前清单中列出的功能问题已经全部修正，剩余内容仅是可继续演进的优化建议，不再属于 V1 完成度阻断项。

## Harness 设计体现

当前项目没有单独命名为 `Harness` 的模块。这里的 harness 设计主要体现为一组可替换、可注入、可测试的运行承载层，用于隔离真实 TUI、真实 LLM、真实文件副作用，让 Agent 核心流程能在无网络、无真实终端交互、无外部服务依赖的情况下稳定验证。

| Harness 点位 | 当前体现 | 作用 | 完成度 |
|---|---|---|---:|
| Test LLM harness | `tests/support.py` 中的测试用 LLM stub；测试通过显式注入 provider 或 stub | 固定模型输出，稳定复现 tool calls、项目介绍、文件写入等演示路径，避免测试依赖真实 API | 已完成 |
| Runtime harness | `AgentRuntime.on_user_message()` 可直接接收 `SessionState` 和用户输入 | 绕过 TUI，直接验证一次用户请求如何转成任务、工具调用、产物和 Agent 回复 | 已完成 |
| Loop 注入 harness | `ReflectionLoop(react_loop=...)`、`ReActLoop(llm, registry)`、`runtime.reflection_loop = Fake...` | 用 Fake/Stub 替换 LLM、ReAct、Reflection，测试异常、超时、边界和失败路径 | 已完成 |
| Tool harness | `ToolRegistry(tools=[...])` 和 `ToolScheduler` | 测试可注册临时工具，验证并行、依赖、未知工具、失败重试和资源冲突，不依赖真实业务工具 | 已完成 |
| 文件系统 harness | pytest `tmp_path` + workspace 路径限制 | 每个测试在临时目录内读写文件，隔离真实项目文件，并验证路径逃逸拒绝 | 已完成 |
| TUI harness | `PromptTui(cwd=tmp_path)` 及独立格式化/滚动/状态渲染函数 | 不启动完整终端 UI，也能测试输入提交、输出格式、过程展示和滚动行为 | 已完成 |
| LLM API harness | `OpenAICompatibleLLMClient` 测试中 monkeypatch `urllib.request.urlopen` | 不发真实网络请求，也能验证 HTTP 错误、畸形响应、tool schema 和消息转换 | 已完成 |
| Reporter/Logger harness | `Reporter(tmp_path / "outputs")`、`EventLogger(tmp_path / "runs")` | 将产物与事件日志输出到临时目录，验证报告分块、脱敏和 trace 记录 | 已完成 |

从质量检测角度看，这套 harness 的价值是：当前 327 个测试大多不是端到端黑盒测试，而是通过可注入边界精确压测 Agent 核心模块。它已经支撑了 ReAct、Runtime、工具协议、上下文配对、LLM 错误包装、TUI 格式化等能力。

但 harness 设计仍有少量可继续增强的点：

- token 预算分层触发和更精细的 fallback 策略，还可以继续做成专门的可注入测试边界。
- 三类 demo 现在主要依托测试路径，若面向演示发布，还可以单独封装成入口。
- 产物版本摘要和局部修改策略还可以继续补强，以便更好覆盖复杂编辑场景。

## 测试清单

| 测试项目 | 优先级 | 完成情况 | 结论 |
|---|---:|---:|---|
| TUI 入口与连续对话 | P1 | 已完成 | `manus-mini` 和 prompt_toolkit TUI 都已接入参数化入口，可连续输入、渲染消息和产物。 |
| 四区布局：消息区/产物区/状态区/输入区 | P2 | 已完成 | 已具备消息区、产物区、状态区和输入区，且能展示过程与确认状态。 |
| CLI 参数：`--cwd`、`--max-steps`、`--max-react`、`--max-reflect`、`--dry-run` 等 | P1 | 已完成 | `argparse` 已接入，CLI 和 TUI 入口都支持参数化运行。 |
| 三层 Loop 基础结构 | P0 | 已完成 | Runtime -> Reflection -> ReAct 已接入，且 Planner / Reflector / Executor / Observer 已拆分。 |
| ReAct 工具循环 | P3 | 已完成 | 能处理 LLM tool_calls、执行工具、回填工具结果、达到上限报错。 |
| Reflection 质量反馈循环 | P0 | 已完成 | 已支持 `accept/local_update/regenerate/replan` 决策，并接入主链路；代码任务会执行 pytest gate，非代码任务下一版补结构化验收。 |
| 工程兜底循环 | P0 | 已完成 | 已支持多工程步推进、超时、异常处理和反思回路。 |
| Planner | P0 | 已完成 | 已有独立 Planner 模块并接入 Runtime，可区分 chat / research / code / automation / report 意图。 |
| Executor | P1 | 已完成 | 工具执行逻辑已内聚为独立 Executor 模块。 |
| Observer | P1 | 已完成 | 已有独立 Observer，将工具结果转换为 Observation。 |
| Reflector | P0 | 已完成 | 已有独立 Reflector，负责草稿质量判断和跳转决策。 |
| ToolScheduler 并行调度 | P2 | 已完成 | 调度器和 batch trace 都已接入，能记录 batch、耗时和并行信息。 |
| 文件工具：`list_files`、`read_file`、`write_file` | P3 | 已完成 | 三个基础文件工具已实现并有测试，`list_files` 会尊重 workspace `.gitignore`，且无 `.gitignore` 时会过滤常见依赖和构建产物。 |
| 文件工具：`append_file`、`make_directory` | P2 | 已完成 | 已实现并接入默认工具注册。 |
| Research Pack | P1 | 已完成 | 已提供 `collect_local_docs`、`summarize_text`、`generate_markdown_report`。 |
| Code Pack | P1 | 已完成 | 已提供 `scan_project`、`read_code_file`、`propose_patch`、`apply_text_edit`。 |
| Automation Pack | P2 | 已完成 | 已提供 `extract_todos`、`organize_notes`、`generate_checklist`。 |
| 文件写入确认 UI | P0 | 已完成 | 已接入确认预览与用户确认流程，不会绕过确认直接写入。 |
| 用户拒绝写入后的替代流程 | P0 | 已完成 | 已接入拒绝确认后的状态回写和替代回复。 |
| `dry-run` 模式 | P0 | 已完成 | 已接入 dry-run 参数和写入拒绝策略。 |
| 路径逃逸限制 | P3 | 已完成 | `resolve_workspace_path()` 限制 workspace 内路径，测试覆盖。 |
| 命令工具风险控制 | P3 | 已完成 | 已有 `run_bash` 和 `run_temp_script`，包含危险模式拒绝、风险确认、超时和输出截断。 |
| 写入前保存原内容摘要 | P2 | 已完成 | 写入/追加工具都会记录 `previous_content_preview`。 |
| 长期记忆存储 | P1 | 已完成 | `MemoryManager` 支持 add/search/filter/delete，并接入 Runtime/TUI。 |
| 记忆注入到模型上下文 | P1 | 已完成 | Runtime 会检索相关记忆并注入对话上下文。 |
| 记忆删除：“忘记偏好” | P2 | 已完成 | 已支持删除单条或全部长期记忆。 |
| 上下文压缩基础 | P1 | 已完成 | `compact_messages()` / `compact_messages_with_snapshot()` 已接入压缩与快照。 |
| ContextBundle 构建 | P1 | 已完成 | Runtime 已构建并记录 ContextBundle。 |
| token 预算触发策略 | P1 | 已完成 | 已实现 70% 触发压缩和 90% 硬裁剪策略，并记录预算日志。 |
| token 预算日志 | P2 | 已完成 | 已记录 `context_budget` 和 `context_bundle` 日志。 |
| 压缩摘要进入 TUI | P2 | 已完成 | 压缩摘要会以系统消息回写到对话区。 |
| 手动保存上下文快照 | P2 | 已完成 | `/save-context` 会在项目根目录写入带时间戳的 `context-*` 快照目录。 |
| 指令帮助 | P3 | 已完成 | `/help` 会输出 TUI 指令和 CLI 指令清单。 |
| 执行日志 `events.jsonl` | P2 | 已完成 | 已记录计划、反思、工具批次、超时和上下文信息。 |
| 对话 list/resume | P1 | 已完成 | `manus-mini list` 可列出会话，`manus-mini resume <session_id>` 可恢复上下文。 |
| `runs/<run_id>/summary.md` | P2 | 已完成 | Reporter 已写入 `runs/<run_id>/summary.md`。 |
| Markdown 产物输出 | P3 | 已完成 | 每轮会写 `outputs/<timestamp>-<run_id>.md`。 |
| 多轮基于当前产物修改 | P1 | 已完成 | 已保留会话历史、产物回写和当前产物上下文注入，可连续基于上一轮结果修改。 |
| 三类任务 demo | P1 | 已完成 | 已有研究、写入、自动化三类端到端测试路径。 |
| 连续两轮修改上一轮产物 demo | P1 | 已完成 | 已有连续两轮写入修改的演示测试。 |
| 三层最大循环次数展示 demo | P2 | 已完成 | 欢迎页集中展示工程、ReAct 和 Reflection 上限，执行过程区不再重复展示固定上限。 |
| 记住用户偏好并在后续回复使用 demo | P1 | 已完成 | 已能保存记忆并在后续轮次注入上下文。 |
| 长会话压缩 demo | P2 | 已完成 | 已有长会话压缩和系统消息回写测试。 |
| 写入确认 demo | P0 | 已完成 | 已有确认/拒绝/继续的完整演示路径。 |
| Runtime 最大步数内正常结束 | P3 | 已完成 | 有测试覆盖。 |
| Runtime 达到最大步数时输出部分结果 | P1 | 已完成 | 已在超出外层工程步数时保留当前最佳结果，并提示下一步建议。 |
| ReAct 达到最大工具循环次数时抛出可重试错误 | P3 | 已完成 | 有测试覆盖。 |
| 并行批次中单个工具失败处理 | P1 | 已完成 | 已保留观察和 batch trace，但还可以继续加强降级提示。 |
| Reflection 四分支测试 | P0 | 已完成 | 已覆盖 `accept/local_update/regenerate/replan`。 |
| 工程兜底处理工具超时 | P1 | 已完成 | 已有工具超时机制和测试。 |
| 工程兜底处理非法 LLM 输出 | P1 | 已完成 | 已在 LLM 输出非法时回退到规则化草稿，避免主链路直接失败。 |
| 工程兜底处理 token 超限 | P1 | 已完成 | 已接入分层预算触发、压缩和硬裁剪，超限时可保留部分结果。 |
| 工具重试耗尽 | P1 | 已完成 | 已标记 `TOOL_RETRY_EXHAUSTED` 并继续进入后续流程。 |
| TUI 输入消息后追加用户消息、触发任务并渲染 Agent 回复 | P3 | 已完成 | 有基础测试覆盖。 |
| 敏感内容不写入长期记忆 | P3 | 已完成 | `MemoryManager.add_if_allowed()` 有过滤测试。 |
| 只读工具不能写文件 | P3 | 已完成 | 只读工具实现和测试覆盖。 |
| 写入工具未确认不修改文件 | P0 | 已完成 | 工具层和应用层都已接入确认保护。 |
| ToolResult 错误转 Observation | P1 | 已完成 | 已由独立 Observer 接管转换。 |
| Reporter 生成 Markdown 摘要 | P3 | 已完成 | 有 Reporter 和测试。 |

## 优先级分组

### P0：主流程与安全边界

当前没有保留的 P0 未完成项。

### P1：V1 核心验收能力

1. token 预算策略已经按文档补齐，后续可继续细化预算模型。
2. 多轮基于当前产物修改已接入主链路，后续可继续增强版本摘要。
3. 工具异常 fallback 已接入规则化兜底，后续可继续补更细的重试策略。

### P2：可观察性、演示与体验完整性

1. 三类 demo 已有对应端到端测试路径，若需要面向演示发布，还可以再包装成独立入口。
2. Automation / Research / Code pack 已可用，若要进一步产品化，还可以接入更完整的工具编排。

### P3：低风险验证与已完成项

1. 保持现有 ReAct、文件工具、路径限制、Reporter、敏感信息过滤等测试覆盖。
2. 后续改动应避免破坏当前 327 个通过测试和 7 个 eval 用例。

## 建议验收顺序

1. 继续收敛 token 预算策略和非法 LLM 输出 fallback。
2. 若要面向演示发布，再把现有测试路径包装成独立 demo 入口。
3. 继续保留当前测试和日志覆盖，避免回退。
