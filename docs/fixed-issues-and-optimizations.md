# Manus Mini 已修复问题与优化记录

更新时间：2026-07-01

本文用于记录 Manus Mini 第一版开发过程中已经修复的问题和完成的体验优化。每条记录包含现象、根因、处理方式和验证方式，方便后续复盘、面试讲解和继续迭代。

## 1. TUI 输入与交互

### 1.1 中文输入无法上屏

- 现象：TUI 输入区最初只能输入英文和数字，后续修到英文、空格、标点可用后，中文仍只能显示拼音，无法直接输入汉字。
- 根因：早期输入处理过度拦截按键，影响了终端输入法的组合输入流程。
- 修复：调整 TUI 输入层，避免拦截 IME 组合输入，让中文输入法完成上屏后再进入输入区。
- 验证：增加输入相关测试，覆盖中文、空格、标点和普通句子输入。

### 1.2 Enter / Shift+Enter 行为不符合对话习惯

- 现象：用户希望 `Enter` 发送消息，`Shift+Enter` 换行。
- 修复：基于 `prompt_toolkit` 增加按键绑定，将 `Enter` 映射为发送，将常见 Shift+Enter 转义序列映射为换行。
- 验证：测试覆盖 Shift+Enter 序列映射和光标位置换行。

### 1.3 发送消息后 UI 明显卡顿

- 现象：消息发出后 TUI 会卡住，体验像同步等待 Agent 执行完成。
- 根因：运行 Agent 的逻辑阻塞在 TUI 事件循环内。
- 修复：将 Agent 执行放到后台任务中，前台持续刷新过程状态。
- 验证：测试覆盖 `send_current_input` 会启动后台任务且不会阻塞输入区。

## 2. TUI 展示优化

### 2.1 对话、过程、产物混杂

- 现象：用户问题、执行过程和最终产物展示层次不清晰。
- 优化：统一输出区结构，分为“用户问题 / 对话记录 / 执行过程 / 最终产物”几个区块。
- 当前行为：运行中展示执行过程；完成后继续保留执行过程，并在后面展示最终产物。
- 验证：测试覆盖运行态 transcript 展示过程、完成态 transcript 同时展示过程和产物。

### 2.2 缺少“当前执行到哪一步”的持续提醒

- 现象：执行过程中用户不知道当前处于规划、调用工具、反思还是产物整理阶段。
- 优化：状态栏和过程区展示当前步骤、阶段、动作、进度和状态。
- 示例：`正在执行 | 第 1/12 步 | ReAct 上限 8 | Reflection 上限 5 | 当前 准备调用工具 read_file(call-read)`
- 验证：测试覆盖状态栏包含当前步骤、阶段和当前动作。

### 2.3 工具调用和工具返回不可见

- 现象：用户不知道 Agent 调用了哪些工具，也看不到工具返回了什么。
- 优化：执行过程区增加“工具活动”，细分为“工具调用”和“工具返回”。
- 展示内容：工具名、tool_call_id、参数、成功/失败状态、摘要、返回预览。
- 验证：测试覆盖 tool call、tool return、observation fallback 的展示。

### 2.4 过程一次性全部刷出来

- 现象：执行过程不是逐步出现，而是最后一次性展示，用户体验不好。
- 优化：增加 `visible_trace_count`，按批次逐步揭示 trace event。
- 当前策略：每次刷新显示更多过程事件，最终产物也按小片段流式输出。
- 验证：测试覆盖 trace event 渐进展示和产物流式展示期间 TUI 仍处于 busy 状态。

### 2.5 工具参数展示像调试 dump

- 现象：参数展示为 `path='README.md'` 这类 Python `repr` 风格，不像用户界面。
- 优化：改为 `path: README.md | limit: 10 | confirmed: true`。
- 同时处理：长文本、嵌套 dict/list 会自动截断，敏感字段继续脱敏。
- 验证：测试覆盖 `format_inline_args`、`format_trace_data` 和敏感信息脱敏。

### 2.6 工具返回缺失 ok 字段时被误判失败

- 现象：部分 trace event 没有 `ok` 字段时，TUI 显示为“失败”。
- 根因：展示层直接用 `event.data.get("ok")` 转 bool，缺失值等同于 False。
- 修复：增加中性状态，“缺失 ok 字段”显示为“已返回”。
- 验证：测试覆盖 `read_file(call-read) 已返回`，且不误显示失败。

### 2.7 产物流式输出时结构丢失

- 现象：最终结果流式输出期间，只展示裸正文，缺少“完成摘要 / 结果正文”的结构。
- 修复：流式产物也走统一 artifact formatter，保留完成摘要和结果正文。
- 验证：测试覆盖 stream 期间仍包含“最终产物 / 完成摘要 / 结果正文”。

### 2.7.1 完成后看不到执行过程

- 现象：任务完成后 TUI 会收起执行过程，只展示最终产物；用户无法回看 Agent 做了哪些事。
- 修复：完成态 transcript 仍保留“执行过程”，并在其后展示“最终产物”。
- 验证：测试覆盖完成态同时包含执行过程和最终产物，且最终结果不会在对话记录中重复展示。

### 2.8 过程内容展示过于原始

- 现象：过程区会把 trace data 中的嵌套结构展示出来，用户看到的是类似 JSON / Python 结构的调试信息。
- 根因：展示层直接格式化 event data，没有按用户视角提炼关键信息。
- 优化：新增事件摘要格式，将过程展示为更清晰的自然语言摘要。
- 示例：
  - `LLM：准备调用：list_files(call-list), read_file(call-read)`
  - `工具返回：read_file(call-read) 成功，read README.md`
- 验证：测试覆盖过程区不展示原始嵌套 JSON，只展示工具名、调用 id、状态和摘要。

### 2.9 用户输入不容易在长对话里定位

- 现象：用户输入和 Agent 输出都只是普通文本，长对话中不容易快速找到用户原始问题。
- 优化：用户消息增加 padding 和块状边框展示，用浅灰风格的视觉分组模拟背景块，方便快速扫描。
- 说明：当前 TUI 基于 `prompt_toolkit.TextArea`，不能对单独某一行直接设置真实背景色，因此第一版用带 padding 的用户消息块实现稳定的可识别效果。
- 验证：测试覆盖用户消息块格式，包括顶部边框、正文 padding 和底部边框。

### 2.10 状态栏上限信息误导用户

- 现象：状态栏显示 `第 1/12 步`，容易被理解为 planner 拆出的第 1 个计划步骤；实际上这里的 12 是外层工程循环上限。
- 根因：状态栏混合展示了运行状态、阶段、当前动作和固定上限配置，信息重复且语义不清。
- 修复：精简状态栏，只保留状态、阶段、当前动作和输入提示。
- 优化：新增欢迎文案，将外层工程循环上限、ReAct 上限、Reflection 上限、工具重试上限和快捷键集中展示。
- 验证：测试覆盖状态栏不再展示 ReAct/Reflection 上限和 `第 n/12 步`，初始输出展示欢迎文案与运行设置。

## 3. TUI 滚动与可读性

### 3.1 输出区可视窗口太小，历史内容不好查看

- 现象：对话过程和产物可见区域偏小，往上滑动不顺畅。
- 优化：将对话、过程、产物合并到一个可聚焦、可滚动的输出区域，输入区保持紧凑。
- 验证：测试覆盖输出区可聚焦，输入区高度受控。

### 3.2 滚动时经常卡死

- 现象：TUI 输出内容较长时，PageUp/PageDown 或滚动容易卡住。
- 根因：每次滚动都会对完整输出执行 `splitlines()`，再从第一行累加字符位置，内容越长越慢。
- 修复：为输出文本维护行起始位置缓存，滚动时通过索引直接跳转。
- 额外优化：用户滚动到历史位置时，后台进度刷新不会重写输出，避免“看历史时被拉回底部”。
- 验证：
  - 20 万行内容连续滚动 1000 次约 `0.0013s`。
  - 测试覆盖大内容滚动复用缓存、起点/中间/终点滚动、阅读历史时不刷新抢占。

### 3.3 流式输出时向上滚动后不再展示后续内容

- 现象：正在流式输出时，如果用户向上滚动查看历史，后续内容不会继续跟随展示；等执行结束后，看起来像一下子全部出现。
- 根因：旧逻辑只根据当前光标是否在底部决定是否跟随，但没有明确维护“用户是否希望跟随底部”的状态。
- 修复：增加 `follow_output` 状态。
  - 用户在底部时，流式输出持续自动向下跟随。
  - 用户滚到历史位置时，输出继续生成但不抢滚动位置。
  - 用户滚回底部后，重新恢复自动跟随。
- 验证：测试覆盖“流式中滚到顶部保持当前位置”和“回到底部后继续跟随输出”。

### 3.3.1 完成后只能在底部几行滚动

- 现象：任务回答完成后，滚动范围像被限制在底部几行，无法向上查看完整用户问题、过程和工具调用信息。
- 根因：完成态 transcript 之前隐藏了执行过程，最终输出内容较短；同时滚动索引需要基于最终完整 transcript 重建。
- 修复：完成态保留执行过程，并在每次 set output 时重建行索引，确保最终输出可从底部滚到顶部。
- 验证：测试覆盖包含大量 trace event 的完成态输出，可以从最终产物滚回顶部看到“用户问题”。

### 3.3.2 完成后仍只能上滑到过程尾部

- 现象：完成后虽然能看到一部分过程，但上滑只能停在过程尾部附近，无法一直回到最顶部查看全部对话和完整过程。
- 根因：完成态 `format_process` 仍按运行态策略只保留最近 8 条 trace event，工具活动也只保留最近 5 条；同时 PageUp/Home 只在输出区聚焦时生效，输入区聚焦时翻页体验不稳定。
- 修复：
  - 完成态展示完整 trace history，不再裁剪最近几条。
  - 完成态工具调用和工具返回不再只保留最近 5 条。
  - PageUp/PageDown/Home/End 改为全局按键，不需要先 Tab 到输出区。
  - PageUp/PageDown 步长从 10 行提升到 30 行。
- 验证：测试覆盖完成态完整过程历史包含首尾 trace，并覆盖大输出快速翻页。

### 3.3.3 项目输出完成后触控板/鼠标滚轮无法向上滚动

- 现象：任务输出完成后，键盘 `PageUp` 可以让展示区向上翻页，但在 iTerm2 中使用触控板或鼠标滚轮向上滑动时，展示区没有反应，用户感觉“项目输出完就向上滚动不了了”。
- 真实根因：
  - `PageUp` 走的是全局键盘快捷键路径，会直接调用 `scroll_output(-30)`。
  - iTerm2 的触控板/鼠标滚轮会发送 xterm SGR mouse event，经 `prompt_toolkit` 解析为 `Keys.Vt100MouseEvent`，再派发到鼠标坐标所在控件的 `mouse_handler`。
  - 自定义工作展示区从 `TextArea` 换成 `FormattedTextControl` 后，虽然键盘滚动逻辑可用，但控件本身没有处理 `MouseEventType.SCROLL_UP / SCROLL_DOWN`，所以滚轮事件命中了展示区后被返回 `NotImplemented`，没有改变滚动位置。
- 修复：
  - 新增 `ScrollableTextControl`，继承 `FormattedTextControl`。
  - 在控件级 `mouse_handler` 中处理滚轮事件：
    - `SCROLL_UP` 调用 `view.scroll(-5)`。
    - `SCROLL_DOWN` 调用 `view.scroll(5)`。
  - 保留键盘 `PageUp/PageDown/Home/End` 逻辑，与鼠标/触控板共享同一个 `scroll_top` 状态。
- 验证：
  - 单元测试覆盖 `MouseEventType.SCROLL_UP / SCROLL_DOWN` 会改变展示区 `scroll_top`。
  - PTY 实跑当前 TUI：使用 mock 任务生成完整输出后，发送真实 xterm SGR 滚轮上滑事件 `\x1b[<64;40;10M`，展示区从“最终产物”滚回“执行过程”；再发送 `\x1b[<65;40;10M`，展示区回到底部“最终产物”。
  - 全量测试：`121 passed`；静态检查：`ruff check src tests` 通过。

### 3.4 过程内容视觉噪声偏重

- 现象：过程区内容较多时，视觉重量和正文接近，影响阅读最终结果。
- 优化：将输出区文字颜色调浅，降低过程信息的视觉噪声。
- 验证：样式配置已更新，并通过 TUI 专项测试和全量测试。

## 4. 异常兜底与运行状态

### 4.1 LLM HTTP 400 等异常直接打爆 TUI

- 现象：LLM 请求失败时，TUI 事件循环抛出未处理异常，需要按 Enter 继续。
- 根因：LLM 异常没有在 runtime / TUI 层统一捕获并转成任务失败状态。
- 修复：LLM client 包装 HTTPError、URLError、Timeout、JSON 解析错误；runtime 捕获异常并写入 task errors、trace events 和最终失败消息。
- 验证：测试覆盖 LLM HTTP 错误包装、runtime 将异常转为 failed message。

### 4.2 执行失败后运行状态未重置

- 现象：任务失败后 TUI 仍可能停留在 running 状态，影响下一次输入。
- 修复：`run_agent_turn` 和 `render_unexpected_error` 在异常路径重置 `is_running`、`is_streaming_artifact`，并把焦点回到输入区。
- 验证：测试覆盖异常后 `is_running is False`，输出区包含“执行失败”。

### 4.3 MAX_REACT_ITERATIONS_REACHED 不易理解

- 现象：ReAct 达到最大循环次数时，用户只看到内部错误码。
- 优化：runtime 将该错误归类为 `MAX_REACT_ITERATIONS_REACHED`，trace 中记录“ReAct iteration limit reached”，方便排查。
- 验证：测试覆盖错误码标记和 runtime trace event。

### 4.4 状态栏完成后仍像在运行

- 现象：任务 done 或 failed 后，状态栏仍可能显示正在执行。
- 修复：状态栏根据 task status 显示“已完成 / 执行失败 / 已结束 / 正在执行”。
- 验证：测试覆盖 done 和 failed 状态不会显示“正在执行”。

## 5. 工具调用与 ReAct 循环

### 5.1 工具调用缺少过程展示

- 优化：ReAct 循环在每轮开始、LLM 返回工具调用、工具调度、工具完成时写入 trace events。
- 效果：TUI 可以实时展示“第几轮 ReAct、调用哪些工具、工具返回什么”。
- 验证：runtime 和 TUI 测试覆盖 trace events 与工具活动展示。

### 5.2 并行工具调度

- 优化：工具调用会先进入 scheduler，能并行的读工具并行执行，有依赖或资源冲突的调用串行执行。
- 价值：减少等待时间，提升 TUI 反馈速度。
- 验证：测试覆盖独立工具并行执行、依赖调用串行、资源冲突串行。

### 5.3 未知工具调用导致流程中断

- 现象：LLM 可能返回不存在的工具名。
- 修复：未知工具不直接打断流程，而是转成失败的 tool observation 返回给 LLM。
- 验证：测试覆盖 unknown tool 会生成 observation，并继续后续 LLM 回合。

### 5.4 工具临时失败缺少重试

- 优化：单个工具调用支持最大重试次数，失败后写入 retry trace，重试耗尽后返回 `TOOL_RETRY_EXHAUSTED`。
- 验证：测试覆盖 transient failure 重试成功和重试耗尽。

### 5.5 LLM 返回空 tool_call_id 或重复 id

- 现象：工具消息需要和 assistant tool_calls 成对，否则 OpenAI-compatible API 会报错。
- 修复：ReAct 对空 id 和重复 id 做规范化，确保每个 tool call 有唯一 id。
- 验证：测试覆盖空 id、重复 id 会被改成唯一 id，tool message 可正确配对。

### 5.6 上下文压缩不能切断 tool call / tool result 对

- 风险：硬切割上下文时，如果留下孤儿 `tool_call_id`，LLM API 会报错。
- 修复：context segment 将 assistant tool call 和对应 tool result 作为一个 `tool_exchange` 保留或一起压缩。
- 验证：测试覆盖 tool exchange 不会被拆开，孤儿 tool result 会被校验拒绝。

## 6. 文件工具与 workspace 安全

### 6.1 write_file 执行失败：`'workspace'`

- 现象：用户请求“在工作目录下新建 helloworld.py 文件”时，最终产物显示 `执行失败：'workspace'`。
- 根因：`write_file` 在调度阶段需要调用 `resource_keys()` 判断资源冲突，但调度发生在真正执行工具之前，当时还没有注入 `workspace`。
- 修复：ReAct 在调度前为已知工具调用注入真实 `session.cwd`，同时继续清理 LLM 传入的伪造 `workspace`。
- 额外处理：Agent 路径中的 `write_file` 自动补 `confirmed=True`，否则真实 LLM schema 不会传确认字段，文件不会写入。
- 验证：
  - 回归测试覆盖 write_file 调度前注入 workspace。
  - 手工复现同类请求成功创建 `helloworld.py`，内容为 `print('hello world')`。

### 6.2 LLM 传入 workspace 参数存在路径污染风险

- 风险：LLM 可能传入 `workspace=/tmp/evil` 一类参数。
- 修复：保留 `sanitize_tool_args`，清理 LLM 提供的 `workspace`，统一使用 session 的真实 cwd。
- 验证：测试覆盖 LLM 传入伪造 workspace 时，工具实际收到的是 `tmp_path`。

### 6.3 文件读取和写入边界保护

- 修复与优化：
  - 禁止读取 workspace 外路径。
  - 拒绝二进制文件读取。
  - 拒绝过大文件读取或写入。
  - 拒绝写入 `.env` 等敏感路径。
  - 写工具底层仍要求确认，防止直接调用绕过安全保护。
- 验证：`tests/test_tools.py` 覆盖路径逃逸、二进制、超大内容、敏感路径和确认逻辑。

## 7. LLM 兼容与配置

### 7.1 增加 `.env` 配置

- 优化：支持通过 `.env` 配置 LLM 请求地址、API key、模型、超时时间和 provider。
- 验证：测试覆盖 `.env` 读取、export 前缀、行内注释和默认 mock provider。

### 7.2 OpenAI-compatible tool call 格式不完整

- 问题：早期请求只发送普通 messages，未完整保留 assistant tool_calls、tool_call_id 和 tool arguments。
- 修复：增加 OpenAI-compatible message 转换，保留 assistant 的 tool_calls 和 tool result 的 tool_call_id。
- 验证：测试覆盖 agent role 映射 assistant、tool schema、非法 tool call 参数处理。

### 7.3 LLM 返回 malformed response 缺少清晰错误

- 修复：解析 LLM 响应时检查 choices、message、tool_calls、function name、arguments JSON 等结构；异常统一包装为 `LLMRequestError`。
- 验证：测试覆盖 HTTP error、malformed success response、缺少工具名、非 object 参数。

### 7.4 MockLLM 支持项目介绍和 helloworld 演示

- 优化：MockLLM 支持“获取当前项目并说明作用”和“新建 helloworld.py”这类面试演示场景。
- 价值：不配置真实 LLM 时，也能稳定演示核心 ReAct + tool flow。
- 验证：runtime 测试和手工命令验证通过。

## 8. 上下文、记忆与输出报告

### 8.1 长上下文压缩

- 优化：实现估算 token、构建上下文 segment、压缩旧消息、保留近期消息和关键摘要。
- 关键约束：tool call 与 tool result 必须成对保留，不能产生孤儿 tool_call_id。
- 验证：`tests/test_context.py` 覆盖 token 估算、压缩、脱敏和 tool exchange 完整性。

### 8.2 长期记忆基础能力

- 优化：增加 SQLite 记忆存储，支持用户偏好、项目摘要、决策和约束的保存与搜索。
- 安全处理：敏感内容不会写入长期记忆。
- 验证：`tests/test_memory.py` 覆盖保存、搜索、敏感信息过滤。

### 8.3 output 文件记录不完整

- 现象：输出文件需要包含用户输入、过程输入、工具观察和最终结果，并且需要分块。
- 修复：Reporter 输出 Markdown 报告，包含用户输入、执行过程、工具观察、最终产物；长内容按 chunk 分块。
- 验证：测试覆盖输出报告内容、分块、脱敏和文件名格式。

### 8.4 output 文件名不便查找

- 优化：输出文件名以时间戳开头，例如 `YYYYMMDD-HHMMSS-run-xxxx.md`。
- 验证：测试覆盖文件名正则。

## 9. 日志与脱敏

### 9.1 运行日志

- 优化：增加 JSONL 事件日志，记录 engineering step、error、result 等事件。
- 验证：测试覆盖日志写入。

### 9.2 敏感信息脱敏

- 优化：对日志、报告、trace data、工具观察、最终产物中的 key、password、token 等敏感内容进行脱敏。
- 验证：测试覆盖日志脱敏、报告脱敏、TUI 过程脱敏。

## 10. 当前验证基线

最近一次完整验证：

```bash
pytest -q
# 117 passed

ruff check .
# All checks passed!
```

补充手工验证：

```bash
LLM_PROVIDER=mock LLM_BASE_URL= LLM_API_KEY= python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from manus_mini.runtime import AgentRuntime
from manus_mini.session import SessionState
from manus_mini.reporter import Reporter
from manus_mini.logging import EventLogger

with TemporaryDirectory() as d:
    cwd = Path(d)
    runtime = AgentRuntime(reporter=Reporter(cwd / "outputs"), logger=EventLogger(cwd / "runs"))
    session = SessionState.create(cwd=cwd)
    result = runtime.on_user_message("在工作目录下，新建一个helloworld.py文件", session)
    task = result.active_task
    print("status=", task.status if task else None)
    print("message=", result.messages[-1].content)
    print("exists=", (cwd / "helloworld.py").exists())
    if (cwd / "helloworld.py").exists():
        print("content=", (cwd / "helloworld.py").read_text(encoding="utf-8").strip())
PY
```

结果：

```text
status= done
message= 已在工作目录下新建 helloworld.py，内容为 `print('hello world')`。
exists= True
content= print('hello world')
```
