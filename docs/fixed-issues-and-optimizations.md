# Manus Mini 已修复问题与优化记录

更新时间：2026-07-03

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
- 优化：统一输出区结构，分为“用户问题 / 执行过程 / 最终产物”几个区块，不再展开重复的对话记录。
- 当前行为：运行中展示执行过程；完成后继续保留执行过程，并在后面展示最终产物。
- 验证：测试覆盖运行态 transcript 展示过程、完成态 transcript 同时展示过程和产物。

### 2.2 缺少“当前执行到哪一步”的持续提醒

- 现象：执行过程中用户不知道当前处于规划、调用工具、反思还是产物整理阶段。
- 优化：状态栏和过程区展示当前步骤、阶段、动作、进度和状态。
- 示例：`正在执行 | 当前 准备调用工具 read_file(call-read)`
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
- 验证：测试覆盖完成态同时包含执行过程和最终产物，且最终结果不会重复展示。

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

### 2.11 过程区重复展示“最多 99 步”

- 现象：欢迎页已经提示最大步数后，执行过程区仍展示“最多 99 步”，信息重复。
- 修复：过程区只展示当前阶段和当前第几步，不再重复展示固定上限。
- 验证：测试覆盖任务概览不包含 `最多 99 步`。

### 2.12 界面缺少上下文占比

- 现象：长会话执行时，用户不知道当前上下文是否接近预算上限。
- 优化：TUI 状态区增加上下文占比展示，基于当前 session 消息估算 token 使用比例。
- 验证：测试覆盖状态栏显示上下文使用比例。

### 2.12.1 上下文占比运行中一直显示 0%

- 现象：任务已经执行多轮 ReAct、产生大量 LLM/tool 过程后，状态栏仍显示 `上下文 0%`。
- 根因：状态栏只统计 `session.messages`，没有统计当前 `active_task` 中的 trace events、tool observations 和运行结果。
- 修复：上下文估算纳入当前任务运行过程，包括 LLM 返回、工具调用参数、工具结果摘要和 observations。
- 验证：测试覆盖消息很少但 active task 过程较多时，上下文占比会随运行过程上升。

### 2.13 Planner 计划与执行过程解释不足

- 现象：Planner 已生成计划，但 TUI 执行过程里看不到完整计划，也不知道当前执行到了哪一步。
- 修复：
  - 执行过程区新增“执行计划”，展示每个 `PlanStep` 的状态。
  - Runtime 在执行前将当前计划步骤标记为 `running`，任务完成后标记为 `done`。
  - TUI 展示使用真实 `PlanStep.status`，不再只由展示层猜测。
- 验证：测试覆盖执行中 plan step 为 `running`，完成后 plan step 为 `done`。

### 2.14 LLM 返回内容和工具调用关系不清晰

- 现象：执行过程只突出工具调用，用户看不到 LLM 为什么要调用这些工具。
- 修复：执行过程改为按 LLM 回合分组展示：
  - 先展示 `LLM 回合 N`。
  - 如果模型返回 `reasoning_content`，展示压缩后的“推理”摘要。
  - 不再展示固定占位的“LLM 返回 / 已返回”。
  - 再展示对应“工具调度”和工具调用。
  - 最后展示“调用进度或结果”。
- 示例：
  - `LLM 回合 1`
  - `推理：需要先确认 README 和 package.json 判断项目类型。`
  - `工具调度：read_file(call-read) path: README.md`
  - `调用进度或结果：read_file(call-read) 成功，read README.md`
- 验证：测试覆盖 LLM 回合、推理摘要、工具调用、调用结果的展示顺序和推理行高亮样式。

### 2.15 上下文占比与真实用量口径不一致

- 现象：状态栏展示的上下文占比来自本地估算，和模型实际返回的 `usage` 结果不一致，用户会看到偏差。
- 修复：
  - 优先读取 LLM 返回里的 `usage.prompt_tokens`，作为上下文占比的真实分子。
  - 分母改为模型上下文长度，DeepSeek v4 系列按 1M token 口径处理。
  - 上下文压缩阈值也统一按同一分母计算，避免“展示一套、压缩另一套”的口径分裂。
- 验证：测试覆盖真实 `usage` 优先、DeepSeek 上下文长度解析，以及状态栏展示与运行时记录使用同一口径。

### 2.16 Human in the loop 确认后页面短暂卡顿

- 现象：用户在 TUI 里点完“确认”后，页面会先卡一下，再回到等待确认状态，体验像是同步阻塞。
- 根因：确认后的续跑逻辑一开始仍然和 UI 刷新强绑定，确认弹层没有明确进入“处理中”状态。
- 修复：
  - 确认动作改为后台续跑，不再在前台同步等待。
  - 确认进行中时隐藏确认弹层，避免界面立即回跳到等待确认。
  - 确认完成后再恢复状态栏和交互焦点。
- 验证：测试覆盖确认后会启动后台续跑，并保持 UI 不阻塞。

### 2.17 TUI 对话记录与最近过程去噪

- 现象：TUI 顶部已经展示“用户问题”，但下面的“对话记录”又重复展示用户输入、空 Agent 消息和工具摘要；同时“最近过程”模块也重复展示过程尾部信息，影响查看关键执行过程。
- 修复：
  - transcript 移除“对话记录”区块，只保留“用户问题 / 执行过程 / 最终产物”。
  - 执行过程区移除“最近过程（折叠）”模块，避免同一批 trace 在多个区域重复出现。
  - 新增 `format_latest_activity()`，把最新动态放到底部状态栏，例如 `最新动态 工具返回：read_file(...) 成功`。
  - 用户滚动查看历史时不强制刷新输出内容，但状态栏仍持续更新最新动态。
- 验证：测试覆盖 transcript 不再展示对话记录、process 不再展示最近过程、状态栏展示最新动态，以及用户查看历史时输出不被改写但状态栏仍更新。

### 2.18 输出区标题过长

- 现象：主输出区标题为“对话 / 过程 / 产物”，与当前已经统一的 transcript 结构重复，视觉上也偏长。
- 修复：将主输出区标题简化为“会话输出”，减少顶部视觉噪声。
- 验证：纳入全量测试回归，确认 TUI 相关测试未受影响。

### 2.19 欢迎页缺少模型配置状态

- 现象：在其他项目目录启动 `manus-mini` 时，如果未命中 LLM 配置，用户只能在发送消息后看到运行失败；如果命中配置，欢迎页也看不到当前使用的模型。
- 修复：
  - 欢迎页新增“模型配置”区域。
  - 找到可用配置时展示当前模型和配置来源路径，例如 `当前模型：deepseek-v4-flash`、`配置来源：/path/to/.env`。
  - 未找到可用配置时提示配置环境变量，或在当前目录 `.env`、`~/.manus-mini/.env`、manus-mini 安装源码根目录 `.env` 中补齐 `LLM_PROVIDER / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL`。
- 验证：`tests/test_prompt_tui.py` 覆盖 formatter 和 TUI 初始化两层展示逻辑。

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
  - 全量测试：`152 passed`；静态检查：`ruff check src tests` 通过。

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

### 4.5 SQLite 连接跨线程访问报错

- 现象：TUI 后台执行消息时出现 `SQLite objects created in a thread can only be used in that same thread`。
- 根因：`PromptTui` 使用 `asyncio.to_thread()` 将 `handle_user_message()` 放到工作线程执行，而 `MemoryManager` 的 SQLite 连接可能在 UI 线程创建。
- 修复：
  - SQLite 连接启用 `check_same_thread=False`。
  - `MemoryManager` 的数据库操作统一加 `threading.RLock`。
- 验证：测试覆盖 `MemoryManager` 跨线程读写访问。

### 4.6 工具失败后 fallback 结果过弱

- 现象：大文件读取可能返回 `FILE_TOO_LARGE`，重试后进入 `TOOL_RETRY_EXHAUSTED`；如果后续 LLM 超时，fallback 结果只回显原始问题，缺少可用信息。
- 修复：规则化 fallback 会优先汇总近期成功的工具观察，尽量保留已有部分结果。
- 验证：测试覆盖 runtime fallback 会包含近期工具观察摘要。

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

### 5.7 循环次数上限偏低

- 现象：复杂任务容易在 ReAct 达到上限后直接失败，用户只能看到内部错误码。
- 修复：
  - 各阶段默认循环/重试上限提升，`MAX_REACT_ITERATIONS_REACHED` 对应的 ReAct 上限设置为 99。
  - ReAct 到上限后不再直接报错，而是切换到最终收口阶段，强制 LLM 基于现有上下文输出结果。
- 验证：测试覆盖 CLI 默认参数、欢迎页运行设置，以及达到 ReAct 上限后仍能返回最终答案。

### 5.8 工具结果过大导致反思循环反复打转

- 现象：真实 LLM 分析项目时，`list_files` / `read_file` 的回传内容如果过长，会把大量路径或全文直接塞进下一轮上下文，模型更容易继续追问更多文件，最后在 `ReAct -> Reflection -> Replan` 链路里反复重跑。
- 根因：工具结果没有做上下文级裁剪，长路径列表和长文本内容会原样进入后续 prompt，导致模型难以稳定判断“已经拿到足够信息”。
- 修复：
  - `list_files` 只保留前若干条路径，超出部分标记为 `truncated`。
  - `read_file` 只回传前若干字符，超出部分标记为 `truncated`。
  - 对空内容显式写出 `[empty]`，避免模型误以为工具消息丢失。
- 验证：补充测试覆盖大规模工具结果会被截断，且截断后的内容仍能让 ReAct 正常收敛，不再持续循环追问。

### 5.9 想法/建议类项目请求会触发过多文件读取

- 现象：用户只是让 Agent “看下项目提优化建议”时，LLM 可能一次性请求大量 `read_file`，把源码全文持续塞入上下文，导致上下文膨胀和工具调用频繁。
- 根因：ReAct 之前只校验工具是否存在，没有限制单轮 tool calls 数量。
- 修复：
  - `LoopLimits` 增加单轮工具预算：总 tool calls、`read_file`、`list_files` 分别设置上限。
  - 超预算工具调用不执行，转成 `TOOL_CALL_BUDGET_EXCEEDED` observation 返回给 LLM。
  - 曾经对 overview 任务增加“只允许读取 README、项目元数据、docs 文档和少量核心入口文件”的限制；后续已放开，详见 9.42。
- 验证：测试覆盖单轮超量工具调用只执行预算内部分。

### 5.10 Planner 没有阻止闲聊触发文件工具

- 现象：用户只是闲聊时，Agent 仍可能调用 `list_files` 查询当前目录。
- 根因：Planner 只生成计划描述，ReAct 仍直接依赖 LLM 决定是否调用工具。
- 修复：
  - Planner 增加 `chat` 意图。
  - Runtime 对明确寒暄/闲聊请求走直接回复路径，不进入 ReAct 工具链。
  - 文件读取、项目分析、写作、修改等任务仍走正常工具链。
- 验证：测试覆盖闲聊不产生 tool trace，且文件读取仍能调用 `read_file`。

### 5.11 Bash 执行与临时脚本能力

- 优化：
  - 新增 `run_bash` 工具，在当前 workspace 中执行 bash 命令，并返回 exit code、stdout、stderr 和执行状态。
  - 新增 `run_temp_script` 工具，把 Agent 生成的 bash 脚本写入系统临时目录，执行完成后自动删除脚本文件。
  - 两个工具都设置为 `command` 风险等级，默认不限制执行时间，只截断输出体积。
  - 如调用方显式传入 `timeout_seconds`，命令和临时脚本仍会按指定秒数超时。
  - LLM tool schema 暴露 `command`、`content`、`is_test`、`timeout_seconds` 和 `output_limit` 参数。
- 验证：测试覆盖 bash 成功/失败、临时脚本成功/失败后删除，以及工具注册和 schema 暴露。

### 5.11.1 命令工具安全边界收紧

- 现象：`run_bash` / `run_temp_script` 具备真实命令执行能力，如果直接继承完整宿主环境或允许明显高风险命令，容易扩大误操作影响面。
- 修复：
  - 命令执行使用受控环境变量，只保留 `PATH`、`HOME`、`PWD`、`LANG`、`LC_ALL` 和必要的 `PYTHONPATH`，避免把 `LLM_API_KEY` 等敏感环境变量暴露给命令。
  - 增加高风险命令拒绝策略，拦截 `sudo`、`rm -rf /`、`mkfs`、`dd if=`、`shutdown`、`reboot` 等模式。
  - `run_bash` 和 `run_temp_script` 共用同一套拒绝规则。
  - 命令被拒绝时保留真实工具名，便于 trace 中区分 `run_bash` 和 `run_temp_script`。
- 验证：测试覆盖危险命令拒绝、临时脚本危险内容拒绝、命令输出中不包含敏感环境变量，以及拒绝结果的 tool name 正确。

### 5.12 代码修改任务增加测试门禁

- 现象：代码修改类任务以前只依赖 LLM/reflection 判断草稿是否满足目标，可能在未执行测试或测试失败时提前接受结果。
- 修复：
  - 执行阶段系统提示要求代码修改、修复、生成或删除任务先准备测试命令或临时测试脚本，修改后必须运行测试。
  - `run_temp_script` 支持 `is_test=true`，用于明确标记代码修改验证脚本；普通临时脚本不会因为工具名相同就自动算作测试门禁。
  - `run_bash` / `run_temp_script` 的 exit code、stdout 和 stderr 会进入 trace event。
  - Reflection 对代码修改任务增加硬门禁：最近一次代码写入、局部替换或目录创建之后，必须至少执行一次测试，且这些测试事件全部通过才允许 `accept`。
  - 如果最新代码修改之后任一测试失败，返回 `regenerate` 并把失败摘要回传；只有修改前的失败测试不会永久阻塞后续已修复并重新通过的结果。
  - 失败信息会通过 runtime 的下一轮修复提示进入上下文，直到测试通过或达到循环上限。
- 验证：测试覆盖未执行测试不接受、最新修改后的任一测试失败不接受、修改前失败但修改后测试通过可接受。

### 5.13 重复工具调用抑制

- 现象：部分 LLM 在连续多轮 ReAct 中会返回相同函数名和相同参数的 `tool_calls`，导致同一工具反复执行，同样的观察结果也被重复塞回上下文。
- 修复：
  - ReAct 为工具调用生成稳定指纹：`tool_name + canonical args`，并去掉 `workspace`、`confirmed` 等运行时参数。
  - `read_file`、`list_files` 等读类工具遇到重复指纹时复用已有成功观察，不再真实执行，也不重复回传完整内容。
  - `write_file`、`replace_in_file`、`run_bash`、`run_temp_script` 等写入或命令类工具遇到重复指纹时返回 `DUPLICATE_TOOL_CALL_BLOCKED`，避免重复写入或重复执行命令。
  - 同一轮内的重复调用和跨轮重复调用都记录 `fingerprint`、`source_tool_call_id`、`deduplicated` 或 `blocked_duplicate`，方便后续排查。
- 验证：测试覆盖跨轮重复 `list_files` 跳过复用，以及跨轮重复 `run_bash` 被阻断且命令只执行一次。

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

### 6.3.1 已有文件支持精确局部替换

- 现象：修改已有文件时，如果总是让 Agent 生成完整文件再调用 `write_file` 全量覆盖，文件较大时会造成明显卡顿，也容易触发文件监听、索引和测试工具的额外刷新。
- 优化：
  - 新增 `replace_in_file` 工具，用 `old_text -> new_text` 做精确替换，避免让 Agent 全量重写已有文件。
  - 默认要求 `old_text` 只匹配 1 处；多处匹配时返回 `REPLACEMENT_COUNT_MISMATCH`，需要显式传入 `expected_replacements` 才允许批量替换。
  - 找不到旧文本时返回 `OLD_TEXT_NOT_FOUND`，避免静默写错文件。
  - 替换结果不变时直接 skipped，不改文件 mtime。
  - 等长替换使用 `r+b` seek 到偏移位置做原地写入；长度变化时使用同目录临时文件 + `os.replace` 原子替换。
  - `replace_in_file` 不需要人工确认，安全性由精确旧文本匹配、替换次数校验和 workspace/protected path 保护保证。
  - 执行提示要求修改已有文件时优先使用 `replace_in_file`，只有创建新文件或必须整体重写时才使用 `write_file`。
- 验证：测试覆盖唯一替换、找不到旧文本、多处匹配保护、显式多处替换、等长原地写入、长度变化原子替换、无变化跳过、工具注册和 LLM schema。

### 6.3.2 局部替换增加上下文校验并收紧全量重写

- 现象：仅依赖短 `old_text` 时，重复片段可能导致替换目标不够明确；同时 `write_file` 仍可能被模型用于覆盖已有大文件，造成卡顿。
- 修复：
  - `replace_in_file` 新增 `before_text` 和 `after_text`，要求旧文本前后上下文精确匹配后才替换。
  - 上下文不匹配时返回 `CONTEXT_MISMATCH`，并返回旧文本出现次数，方便下一轮修正。
  - `write_file` 覆盖已有大文件时默认返回 `FULL_REWRITE_REQUIRES_ALLOW`，提示优先使用 `replace_in_file`。
  - 确实需要整体重写时，必须显式传入 `allow_full_rewrite=true`。
- 验证：测试覆盖上下文定位替换、上下文不匹配拒绝、大文件全量覆盖默认拒绝，以及显式允许后可整体重写。

### 6.3.3 replace_in_file 执行前展示 diff

- 现象：`replace_in_file` 不需要人工确认，虽然能减少卡顿和交互成本，但 TUI 里缺少执行前 diff，用户不容易看到即将发生的局部修改。
- 修复：
  - Executor 在执行 `replace_in_file` 前生成 unified diff preview，并写入 `tool` trace event。
  - TUI 工具活动区识别 `diff_preview` trace，在对应 tool call 下展示“变更预览”及 diff 内容。
  - 该预览只展示，不阻塞执行；`replace_in_file` 仍保持无需人工确认。
- 验证：测试覆盖 `replace_in_file` 执行前记录 diff preview，且 TUI process 区会渲染该 diff。

### 6.4 list_files 未尊重 `.gitignore`

- 现象：Agent 分析项目时，`list_files` 会把 `outputs/`、`runs/`、`build/`、`.manus-mini/` 等运行产物也列入结果，导致文件列表过大、上下文膨胀，并增加 LLM 请求失败概率。
- 根因：文件枚举只过滤了少量固定噪声目录，没有读取 workspace 根目录的 `.gitignore`。
- 修复：
  - `list_files` 加载 `.gitignore` 规则。
  - 支持常见目录规则、通配符规则和否定规则，例如 `outputs/`、`*.log`、`.env.*`、`!.env.example`。
  - 当前项目实测 `list_files` 从 1930 个文件降到 65 个文件，并过滤 `outputs/`、`runs/`、`build/`。
- 验证：测试覆盖 `.gitignore` 中的目录、通配符和否定规则。

### 6.5 无 `.gitignore` 时默认忽略常见依赖和构建产物

- 现象：部分项目没有 `.gitignore` 或规则不完整时，`list_files` 仍可能扫出大量依赖、缓存和构建产物。
- 修复：扩充默认噪声目录，覆盖常见生态：
  - Python：`__pycache__`、`.venv`、`.tox`、`.nox`、`.pytest_cache`、`htmlcov`。
  - JS/前端：`node_modules`、`dist`、`build`、`.next`、`.nuxt`、`.turbo`、`.parcel-cache`。
  - Java/JVM：`target`、`.gradle`、`.mvn`。
  - Go/.NET/通用：`vendor`、`pkg`、`bin`、`obj`、`out`、`coverage`、`.cache`。
  - IDE/本地运行产物：`.idea`、`.vscode`、`runs`、`outputs`、`.manus-mini`。
- 验证：测试覆盖无 `.gitignore` 时 Java/JS/Go/Python 等依赖和构建目录会被过滤。

### 6.6 系统 `/tmp` 临时目录被误判为越界路径

- 现象：某些工具在处理系统临时目录下的绝对路径时，会被统一的 `PATH_OUT_OF_WORKSPACE` 检查拦截，导致无法正常读写 `/tmp` 下的临时文件。
- 根因：路径校验只允许 workspace 内路径，没有把系统临时目录作为明确例外。
- 修复：
  - `resolve_workspace_path()` 允许系统 `/tmp` 目录作为例外放行。
  - 文件工具在展示 `written_path` 和目录结果时，对 workspace 外但被允许的路径保留绝对路径，避免再次依赖相对路径转换。
- 验证：测试覆盖 `/tmp` 绝对路径的解析和写入，确认仍然保留 workspace 边界保护，但允许系统临时目录。

## 7. LLM 兼容与配置

### 7.1 增加 `.env` 配置

- 优化：支持通过 `.env` 配置 LLM 请求地址、API key、模型、超时时间和 provider。
- 验证：测试覆盖 `.env` 读取、export 前缀、行内注释和显式 provider 配置。

### 7.2 OpenAI-compatible tool call 格式不完整

- 问题：早期请求只发送普通 messages，未完整保留 assistant tool_calls、tool_call_id 和 tool arguments。
- 修复：增加 OpenAI-compatible message 转换，保留 assistant 的 tool_calls 和 tool result 的 tool_call_id。
- 验证：测试覆盖 agent role 映射 assistant、tool schema、非法 tool call 参数处理。

### 7.3 LLM 返回 malformed response 缺少清晰错误

- 修复：解析 LLM 响应时检查 choices、message、tool_calls、function name、arguments JSON 等结构；异常统一包装为 `LLMRequestError`。
- 验证：测试覆盖 HTTP error、malformed success response、缺少工具名、非 object 参数。

### 7.4 测试提示词支持项目介绍和 helloworld 演示

- 优化：测试用 LLM stub 支持“获取当前项目并说明作用”和“新建 helloworld.py”这类演示场景。
- 价值：在不依赖真实外部 LLM 的测试场景中，也能稳定演示核心 ReAct + tool flow。
- 验证：runtime 测试和手工命令验证通过。

### 7.5 移除默认 mock provider

- 现象：早期代码在未配置 `LLM_PROVIDER` 时会默认回落到 mock provider，容易让本地运行和生产配置混淆。
- 修复：删除内置 `MockLLMClient` 和默认 mock 回退，`get_default_llm_client()` 现在只接受显式的 `openai-compatible` provider。
- 影响：运行时必须显式配置真实 provider，测试则改为各自注入专用 stub，不再共享默认 mock。
- 验证：测试覆盖未配置 provider 时直接报错，以及显式 `openai-compatible` 配置可正常创建客户端。

### 7.5 LLM 请求默认超时时间偏短

- 现象：真实 LLM 请求经常出现 `LLM request timed out`，尤其是长上下文、多工具结果或模型响应较慢时。
- 根因：默认 `LLM_TIMEOUT_SECONDS=30` 偏保守，复杂任务容易在模型仍在生成时被本地 HTTP 客户端中断。
- 修复：
  - 默认 LLM 请求超时提升到 120 秒。
  - `.env.example`、README 示例和本地 `.env` 同步更新为 `LLM_TIMEOUT_SECONDS=120`。
  - 保留环境变量覆盖能力，用户仍可按需调大或调小。
- 验证：测试覆盖未配置 `.env` 时默认超时为 120 秒。

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

### 8.5 手动压缩上下文

- 优化：新增手动压缩上下文指令：`压缩上下文`、`手动压缩上下文`、`/compact`、`compact context`。
- 行为：触发后压缩当前会话历史，并把压缩摘要作为系统消息回写到对话区。
- 验证：测试覆盖手动压缩命令识别、压缩摘要生成和 session 消息更新。

### 8.5.1 上下文占比与自动压缩口径统一

- 现象：TUI 状态栏的上下文占比、runtime 日志里的压缩触发比例、自动压缩使用的估算口径不一致，容易出现“状态栏已经很高，但自动压缩没有同步触发”的错觉。
- 修复：抽取共享上下文使用率计算函数，TUI 和 runtime 共用同一口径；自动压缩只基于会话消息估算，不再混入运行过程的 trace 细节。
- 行为：状态栏展示的百分比与压缩触发百分比保持一致，方便判断当前会话是否接近预算上限。
- 验证：测试覆盖上下文占比在短消息 + 长运行过程场景下的展示一致性。

### 8.5.2 执行过程中的最近过程模块移除

- 优化：执行过程不再展示“最近过程”模块，避免和 LLM 回合、工具调度、工具返回重复。
- 行为：最新过程摘要移动到底部状态栏的“最新动态”，主视图只保留执行计划、当前步骤和结构化工具过程。
- 验证：测试覆盖执行过程不再包含“最近过程（折叠）”，状态栏展示最新动态。

### 8.6 对话恢复能力

- 优化：新增会话持久化与恢复能力。
- 行为：
  - 每轮 `SessionManager.handle_user_message()` 结束后保存完整 `SessionState`。
  - 会话文件位于 `~/.manus-mini/projects/<project_key>/sessions/<session_id>.json`。
  - 不同项目按项目绝对路径生成不同 `project_key`，避免多个项目共用同一份 session。
  - `manus-mini list --cwd <目录>` 列出当前工作目录下已有会话。
  - `manus-mini resume <session_id> --cwd <目录>` 恢复上次上下文并进入 TUI。
- 验证：测试覆盖 session 保存、加载、列表展示和 resume 入口加载历史消息。

### 8.7 手动保存上下文快照

- 优化：新增 `/save-context` 指令，方便学习和复盘当前上下文。
- 行为：
  - 在项目根目录创建 `context-YYYYMMDD-HHMMSS` 目录。
  - 写入 `session.json` 和便于阅读的 `context.md`。
  - 同一秒重复保存时追加序号，避免覆盖。
- 验证：测试覆盖快照目录、`session.json`、`context.md` 和系统提示消息。

### 8.8 指令帮助

- 优化：新增 `/help` 指令，输出当前可执行指令与作用。
- 覆盖内容：`/help`、`/save-context`、`/compact`、`忘记 <关键词>`、`确认/取消`、`manus-mini list`、`manus-mini resume`。
- 验证：测试覆盖 `/help` 输出包含核心 TUI 指令和 CLI 指令。

### 8.9 长期记忆系统消息不直接展示

- 现象：长期记忆会被注入到 session 里，如果直接展示，会把历史偏好和项目摘要和当前对话混在一起。
- 修复：
  - TUI 渲染时过滤掉内部长期记忆系统消息，只保留真正的用户、Agent 和工具相关内容。
  - 对外显示时不再把 `长期记忆:` 这类内部提示暴露到对话记录里。
- 验证：测试覆盖长期记忆消息不会出现在渲染结果中。

### 8.10 新会话默认不加载旧的磁盘长期记忆

- 现象：新开 TUI 时，如果默认直接读取磁盘里的记忆库，容易把上一次会话的偏好带进来，影响干净启动。
- 修复：没有传入历史 session 时，`PromptTui` 使用内存型 `MemoryManager`；只有恢复历史 session 时才接入磁盘记忆库。
- 价值：新会话默认更“干净”，也避免测试场景把长期记忆写进工作区。
- 验证：测试覆盖新建 TUI 不会读取已有磁盘长期记忆。

### 8.11 CLI 历史目录可见性

- 现象：`manus-mini list --cwd <目录>` 无历史时只显示 `No saved sessions.`，用户不知道它实际读取的是哪个项目隔离目录。
- 修复：无历史时同步输出 `Session directory: <path>`，直接展示当前 `cwd` 对应的 sessions 路径。
- 验证：测试覆盖无历史时输出实际 session directory。

## 9. 日志与脱敏

### 9.1 运行日志

- 优化：增加 JSONL 事件日志，记录 engineering step、error、result 等事件。
- 验证：测试覆盖日志写入。

### 9.2 敏感信息脱敏

- 优化：对日志、报告、trace data、工具观察、最终产物中的 key、password、token 等敏感内容进行脱敏。
- 验证：测试覆盖日志脱敏、报告脱敏、TUI 过程脱敏。

### 9.3 自动测试默认不落日志

- 现象：自动测试会在项目目录产生运行日志，污染工作区。
- 修复：pytest 默认设置 `MANUS_DISABLE_LOGGING=1`；`EventLogger` 在 pytest 或禁用环境变量下默认不写文件，只有显式 `enabled=True` 的日志测试才写入临时目录。
- 验证：测试覆盖日志禁用时不创建日志文件，日志专项测试仍可显式启用。

### 9.3.1 pytest 下运行产物与日志输出隔离

- 现象：测试执行时，`runs/` 目录下的报告和事件日志容易落到仓库工作区，干扰本地开发和 `git status`。
- 修复：
  - `Reporter` 写入的 run summary 文件名改为 `summary-YYYYMMDD-HHMMSS.md`，避免同一 run 目录下的产物重名。
  - pytest 环境下默认的 `AgentRuntime()` 报告输出切到系统临时目录，避免在仓库根目录生成 `outputs/` 或 `runs/`。
- 验证：测试覆盖带时间戳的 run summary 文件名，以及 pytest 环境中默认 runtime 不在当前工作区写出产物。

### 9.4 LLM 原始入参和返回结果日志

- 优化：`LLMResult` 增加 `source_request` 和 `source_response`，OpenAI-compatible client 保留请求 payload 与原始响应结构。
- 日志：ReAct 流程写入 `llm_request` 和 `llm_response` 事件，便于排查真实模型调用问题。
- 验证：测试覆盖 OpenAI-compatible client 暴露源数据，以及 runtime 日志记录 LLM 入参与返回。

### 9.5 读文件结果不应落入日志

- 现象：`read_file` 成功读取的文件正文会通过 trace `content_preview` 或下一轮 LLM request 日志进入 JSONL，日志体积大且不利于隐私控制。
- 修复：
  - `read_file` 成功时，tool trace 只记录工具名、调用 id、参数、状态和摘要，不记录文件内容预览。
  - LLM request 日志中的成功 `read_file` tool result 替换为 `[read_file result omitted from logs]`。
  - `read_file` 失败时仍保留错误摘要和 `error_code`，便于排查。
- 验证：测试覆盖成功读取文件内容不会进入日志，且日志保留省略标记。

### 9.6 CLI 子命令与 TUI 入口统一

- 现象：`manus-mini list` 之前会直接进入 TUI，用户无法先查看已有会话再决定是否恢复。
- 修复：
  - `manus-mini list` 作为独立子命令列出当前工作目录下的会话。
  - `manus-mini resume <session_id>` 恢复历史会话后再进入 TUI。
  - 默认 `manus-mini` 仍然直接进入交互模式，避免破坏原有使用习惯。
- 验证：测试覆盖 `list` 不会打开 TUI，`resume` 会加载会话并进入交互界面。

### 9.7 运行日志按会话和时间戳归档

- 现象：运行日志和报告文件名不够直观，查找具体一次执行记录时成本较高。
- 修复：
  - 事件日志目录改为 `runs/<session_id>-<run_id>/`。
  - 事件文件名以时间戳开头，例如 `YYYYMMDD-HHMMSS-ffffff-event.jsonl`。
  - pytest 场景默认不生成仓库内运行日志，避免污染工作区。
- 验证：测试覆盖新的目录结构和时间戳文件名，且测试环境不会额外落盘。

### 9.8 tool 历史与 LLM 请求内容保持一致

- 现象：工具调用和工具返回虽然已经执行，但历史消息没有稳定回写到 session，导致后续轮次和反思重启时容易“失忆”。
- 修复：
  - tool call assistant message 和 tool result message 都写回 `session.messages`。
  - LLM 请求日志直接记录实际发给模型的消息，不再额外拼接一套不一致的历史。
  - 这样可以保证“传给 LLM 什么，日志就落什么”。
- 验证：测试覆盖工具历史进入 session，以及日志中的 request payload 与实际请求一致。

### 9.9 Ctrl+C 中断兜底

- 现象：执行过程中如果用户强行 `Ctrl+C`，容易留下半截任务状态、日志不完整或残留的孤儿 tool 节点。
- 修复：
  - 捕获中断后及时写入 interrupt 事件。
  - 更新当前任务状态，避免日志和 UI 停留在运行中。
  - 清理未闭合的 tool 执行痕迹，减少孤儿节点问题。
- 验证：测试覆盖中断路径下状态落盘和日志更新。

### 9.10 ReAct 工具预算与项目范围收敛

- 现象：LLM 在一轮里可能一次性请求过多工具，或者在“看项目 / 提建议”这类任务里直接钻到任意源码深处，导致过程噪声大、上下文膨胀快。
- 修复：
  - 增加每轮工具调用预算，默认单轮最多 5 个 tool calls。
  - 细化 `read_file` 和 `list_files` 的单轮上限，避免短时间内重复刷同一类工具。
  - 对“项目概览 / 优化建议 / 想法总结”类任务，`read_file` 先收敛到 README、项目元数据、docs 和核心入口文件。
  - 超出预算或范围的调用不再直接执行，而是转成明确的 observation 返回给 LLM。
- 验证：测试覆盖工具预算耗尽和项目概览范围拦截场景。

### 9.11 达到 ReAct 上限后强制收口

- 现象：当 ReAct 轮数跑满时，流程原来会直接报错或卡在内部错误状态，用户看不到可用结果。
- 修复：
  - 达到 `max_react_iterations` 后，不再直接失败。
  - 改为追加一次无工具收口提示，让模型基于现有上下文输出最终答案。
  - 如果模型仍返回工具请求，也会被忽略，保证流程能结束。
- 验证：测试覆盖 `max_react_iterations=0` 时也能返回最终答案，而不是抛错。

### 9.12 工具结果展示收敛

- 现象：`read_file` 的原始文件正文会在对话区直接展开，长内容会淹没关键信息。
- 修复：
  - 对话区里的 `tool` 消息只展示摘要。
  - 如果结果包含文件正文，折叠成类似 `[README.md 文件内容获取成功]` 的提示。
  - 工具结果本身仍会保留在原始日志和观察记录里，不影响排查。
- 验证：测试覆盖文件正文不会再直接出现在对话记录中。

### 9.13 run summary 与原始事件日志同目录归档

- 现象：原来的 run summary 只按 `run_id` 建目录，和带 `session_id` 的原始事件日志不在同一层，查一次执行要来回找。
- 修复：
  - summary 目录改为 `runs/<session_id>-<run_id>/`。
  - 原始事件日志仍保留在同目录下的 `...-event.jsonl` 中，summary 只是聚合视图，不替代原始数据。
  - 目录名同时带 `session_id` 和 `run_id`，便于按会话追踪。
- 验证：测试覆盖 summary 目录与事件日志目录统一。

### 9.14 项目代码请求先看目录结构摘要

- 现象：用户一旦问到项目代码，模型容易直接跳到 `list_files` 或 `read_file`，先手工扫一遍目录再决定下一步，导致起手式不稳定。
- 修复：
  - 新增项目代码目录结构摘要函数，先生成带说明的只读结构预览。
  - 对所有涉及项目代码的请求，先把这份结构摘要作为系统上下文交给 LLM。
  - LLM 再根据结构摘要决定先看哪些目录或文件，再发起 `list_files` / `read_file`。
  - 该摘要会在会话内刷新，只保留最新一份，避免重复堆积。
- 验证：测试覆盖项目请求时会注入目录结构摘要，且摘要中包含 `src/`、`docs/`、`tests/` 等关键目录说明。

### 9.15 Reflection 改为 LLM 审查

- 现象：Reflection 之前主要依赖硬编码规则，遇到“当前项目是什么”这类问题时，如果草稿错误地要求用户再提供项目描述、链接或代码，规则不一定能兜住。
- 根因：规则型 `Reflector` 只能匹配固定关键词，缺少对用户目标、执行计划、工具观察和当前项目上下文的综合判断。
- 修复：
  - `ReflectionLoop` 优先调用 LLM 进行反思审查。
  - Reflection prompt 包含用户目标、当前工作目录、执行计划、最近工具观察、项目目录结构和待审查草稿。
  - LLM 必须返回结构化 JSON：`accept`、`local_update`、`regenerate` 或 `replan`。
  - 原规则 `Reflector` 保留为 LLM 不可用、返回非法 JSON 或异常时的兜底。
- 验证：测试覆盖 Reflection 会调用 LLM 并使用 LLM 返回的决策和原因。

### 9.16 Reflection 请求、响应和审查上下文完整落日志

- 现象：日志里只能看到 reflection 的 `decision`、`reason` 和 `draft_preview`，排查时不知道反思到底看了哪些上下文。
- 修复：
  - `llm_request` / `llm_response` 增加 `stage=reflection`，记录实际发给模型的 reflection API 请求参数和原始响应。
  - `reflection` 事件增加完整 `draft`。
  - `reflection` 事件增加 `reflection_context`，包含用户目标、工作目录、任务状态、执行计划、工具观察和错误列表。
- 验证：测试覆盖 reflection 日志中包含完整草稿和结构化上下文。

### 9.17 read_file 重复调用去重

- 现象：ReAct 多轮中 LLM 会反复请求读取同一个文件；有时同一轮也可能返回多个相同 `read_file` 调用，导致过程噪声增加、上下文膨胀和不必要的文件读取。
- 根因：
  - Prompt 里只有“避免重复读取”的软约束。
  - 调度器只按依赖和风险分批，不识别 `read_file(path, encoding)` 的语义重复。
- 修复：
  - 跨轮去重：如果同一路径和编码的 `read_file` 已经成功读取过，后续同样调用不再执行真实工具，直接返回“已复用历史读取结果”。
  - 同轮去重：如果 LLM 一次返回多个相同 `read_file(path, encoding)`，后续重复调用直接跳过。
  - 保留合理重试：如果之前读取失败，例如 `FILE_TOO_LARGE`，允许 LLM 调整 `max_bytes` 后再次读取。
  - 去重结果仍按 tool message 返回，保证 assistant tool_calls 与 tool result 的连续性不被破坏。
- 验证：测试覆盖跨轮成功读取去重、同轮重复读取去重，以及 `FILE_TOO_LARGE` 后扩大 `max_bytes` 的重试路径。

### 9.18 read_file 支持按偏移分片读取

- 现象：读取较大文件时，如果文件大小超过 `max_bytes`，工具只能返回 `FILE_TOO_LARGE`，LLM 无法按片段继续查看文件后续内容。
- 修复：
  - `read_file` 增加 `start_index` 参数，表示从文件第几个字节开始读取。
  - `max_bytes` 在传入 `start_index` 时作为本次分片读取长度。
  - 返回结果增加 `start_index`、`bytes_read`、`file_size` 和 `truncated` 元数据，方便 LLM 判断是否继续读取下一段。
  - LLM tool schema 同步暴露 `start_index`，让模型知道可以分片读取大文件。
  - `read_file` 去重 key 纳入 `start_index/max_bytes`，避免不同分片被误判为重复读取。
- 验证：测试覆盖从指定偏移读取指定长度、偏移超出文件大小报错，以及分片读取不破坏重复读取去重。

### 9.19 默认运行数据迁移到用户目录并按项目隔离

- 现象：默认 `runs/`、`outputs/`、`.manus-mini/sessions` 和 `.manus-mini/memory.db` 会落在当前工程目录下，容易污染用户正在分析的项目；如果简单放到 `~/.manus-mini` 根目录，又会导致多个项目互相混用数据。
- 修复：
  - 默认事件日志目录改为 `~/.manus-mini/projects/<project_key>/runs`。
  - 默认 run summary 也写入同一个项目隔离 runs 根目录。
  - 默认报告产物目录改为 `~/.manus-mini/projects/<project_key>/outputs`。
  - session 文件改为 `~/.manus-mini/projects/<project_key>/sessions`。
  - persistent memory 改为 `~/.manus-mini/projects/<project_key>/memory.db`。
  - `project_key` 由项目目录名和项目绝对路径 hash 组成，兼顾可读性和同名项目隔离。
  - 初始化 session store 时会非破坏性迁移旧项目内 `.manus-mini/sessions/*.json` 和 `.manus-mini/memory.db`，只复制缺失文件，不覆盖新项目隔离目录中已有数据。
  - 显式传入 `EventLogger(path)` 或 `Reporter(output_dir)` 时仍尊重调用方指定路径，方便测试和嵌入场景。
  - pytest 环境使用系统临时目录，避免测试污染真实用户 home。
  - session 删除/清空时同步清理对应项目隔离 runs 下的运行日志。
- 验证：测试覆盖项目隔离目录生成、session 保存路径、memory 路径、旧项目 `.manus-mini` 非覆盖迁移、runtime 默认 run summary 目录、session 清理项目 runs，以及工作区不生成默认 runs/outputs。

### 9.20 事件日志内容去重精简

- 现象：LLM 请求/响应日志里同时保存 `request`、`api_request_payload`、`response`、`api_response_raw`，其中不少字段内容重复；reflection context 中 observation 也可能携带大段文件正文。
- 修复：
  - `EventLogger.record()` 写盘前统一压缩事件。
  - 当 `api_request_payload` 与 `request` 相同时删除重复字段。
  - 当 `api_response_raw` 与 `response` 相同时删除重复字段。
  - `llm_response` 事件只保留 response 核心内容，不重复携带 request。
  - reflection observation 的完整正文改为 `content_preview` 和 `content_omitted`，避免长文件内容重复写入日志。
- 验证：测试覆盖 LLM 响应日志不再包含重复 payload 字段，以及 reflection observation 正文被压缩为预览。

### 9.21 本地运行产物清理

- 现象：项目目录下残留 `.pytest_cache`、`.ruff_cache`、`__pycache__` 和 `outputs/`，虽然已被 `.gitignore` 忽略，但会影响本地扫描、Agent 目录判断和人工查看。
- 修复：清理项目内运行产物，保留 `.gitignore` 规则继续防止后续误入库。
- 验证：清理后使用 `find` 检查项目三层内不再存在 `.pytest_cache`、`.ruff_cache`、`__pycache__`、`*.pyc`、`outputs`、`runs`。

### 9.22 工具 schema 下沉到工具实现

- 现象：LLM tool schema 原来集中写在 `llm.py`，而工具实现分散在各 tool 文件，参数变更时容易出现 schema 与实际实现漂移。
- 修复：
  - `BaseTool` 增加 `parameters_schema()`。
  - 默认工具在各自类中声明 schema。
  - `llm.tool_schema(name)` 改为从 `ToolRegistry` 中读取对应工具的 `parameters_schema()`。
- 验证：测试覆盖 `tool_schema("replace_in_file")` 与注册工具自身 schema 完全一致。

### 9.23 TUI 格式化逻辑拆分

- 现象：`prompt_tui.py` 超过 1400 行，同时包含文本格式化、滚动视图、按键绑定和 TUI 状态机，后续维护成本高。
- 修复：
  - 新增 `prompt_tui_formatting.py`，承载 transcript、process、status、markdown 渲染、软换行、样式片段等纯格式化逻辑。
  - `prompt_tui.py` 保留 TUI 控件、状态和事件循环逻辑。
  - `prompt_tui.py` 继续 re-export 原有格式化函数，保持测试和外部导入兼容。
- 验证：`tests/test_prompt_tui.py` 全部通过。

### 9.24 TUI 状态栏语义与 diff 颜色

- 现象：
  - 状态栏曾统一用 `✔ 状态 ...` 前缀，失败或运行中状态也会显示对勾，语义容易误导。
  - 代码 diff 预览在 TUI 主输出区和确认面板中只有普通文本颜色，新增/删除行不够醒目。
- 修复：
  - 状态栏首段改为 `状态 ...`，继续保持主状态位于第一段，避免测试只用包含匹配掩盖位置变化。
  - 主输出区识别“变更预览”后的统一 diff 行，`+` 新增行使用绿色背景，`-` 删除行使用红色背景，文件头和上下文行使用独立样式。
  - 确认面板的 diff 新增/删除行也改为背景色展示，和主输出区保持一致。
- 验证：新增/更新 `tests/test_prompt_tui.py` 覆盖状态栏首字段、失败状态不显示对勾，以及主输出区/确认面板 diff 增删行样式。

### 9.25 移除单轮运行总时长限制

- 现象：Runtime 曾通过 `max_runtime_seconds=180` 限制单轮用户消息最多运行 180 秒，长任务会返回 `Runtime timeout reached`，即使单个工具没有超时也会被外层总时长截断。
- 修复：
  - 删除 `LoopLimits.max_runtime_seconds`。
  - 删除 Runtime 中的总时长检查、`RUNTIME_TIMEOUT` 错误码和运行超时结果文案。
  - TUI 欢迎页不再展示“单轮运行超时”。
  - 产品设计、技术设计、计划文档和摘要同步移除 180 秒/3 分钟运行时长限制描述。
- 后续调整：单工具执行默认也不再限时，详见 9.40。
- 验证：测试覆盖 `LoopLimits` 不再暴露 `max_runtime_seconds`、欢迎页不再展示单轮运行超时，以及慢反思流程不会因总时长失败。

### 9.26 Planner / ReAct / Reflection 提示词收敛

- 现象：真实模型在“看项目/改代码/给建议”类任务中容易计划过宽、重复读取文件、未充分定位就修改，Reflection 也可能接受过于空泛的草稿。
- 优化：
  - Planner 阶段要求计划最多 4 步，每步必须有可验证产出；先定位目标模块，再决定是否读取文件，避免把“查看项目”扩成全仓扫描。
  - ReAct 阶段要求先定位最小相关文件，已有上下文足够时直接推进；没有读取原文件前不得凭空改写已有文件；最终答复说明改了什么、验证了什么。
  - Reflection 阶段明确空泛草稿不能 accept，`reason` 必须指出下一步应补什么，例如读取范围、修正结论或运行验证。
- 验证：`tests/test_planner_reflector.py` 和 `tests/test_runtime.py` 覆盖新提示词约束会进入实际 LLM 上下文。

### 9.27 TUI diff 背景色未生效

- 现象：代码 diff 在 TUI 中仍只显示普通文本，没有按新增/删除行展示绿色或红色背景。
- 根因：diff 输出有时作为普通 process 文本进入渲染链路，原来的样式识别只覆盖了部分结构化片段；同时样式使用不够直接，终端主题下不容易看出背景。
- 修复：
  - `style_output_fragments()` 对独立 process 文本也执行 diff 行识别。
  - 新增行使用绿色背景，删除行使用红色背景，并同步覆盖确认预览中的 diff 展示。
- 验证：`tests/test_prompt_tui.py` 覆盖新增/删除行背景色，以及普通 process 文本中的 diff 高亮。

### 9.28 历史会话中的 `RUNTIME_TIMEOUT` 导致 `manus-mini list` 崩溃

- 现象：移除单轮运行总时长限制后，旧 session 里保存过 `RUNTIME_TIMEOUT` 错误码，`manus-mini list` 读取历史会话时触发 Pydantic Literal 校验失败。
- 根因：运行时错误码删除过于激进，没有考虑历史 JSON 兼容。
- 修复：运行时不再生成 `RUNTIME_TIMEOUT`，但 `AgentError.code` 保留该取值用于历史会话反序列化。
- 验证：`tests/test_session_store.py` 覆盖旧会话 JSON 可被正常列出。

### 9.29 日志复盘发现的路径猜测、测试门禁和日志膨胀问题

- 现象：真实运行日志中模型先读取了仓库根目录下不存在的 `prompt_tui.py` / `prompt_tui_formatting.py`，随后重复读取、grep、sed；代码修改后只做语法检查就被反思接受，且 JSONL 单行可达数万字符。
- 根因：
  - LLM 会根据文件名猜路径，但项目源码实际在 `src/manus_mini/`。
  - Reflection 原先主要通过任务文案识别代码修改，遇到“优化 TUI 展示”这类 UI 表述时可能漏判。
  - LLM request/response 与 reflection observations 中保存了过多正文和重复上下文。
- 修复：
  - Executor 对 `read_file` / `list_files` 的不存在路径做唯一文件名纠偏，并在 trace 中记录 `path_rewritten`。
  - ReAct 在最近一次代码写入后检测到测试通过时直接请求最终答复，避免继续无意义工具循环。
  - Reflection 只要看到成功代码写入事件，就要求写后测试通过；语法检查不再被当作测试通过。
  - EventLogger 压缩 LLM 大消息内容，并限制 reflection context 只保留最近观察。
- 验证：`tests/test_runtime.py`、`tests/test_logging.py`、`tests/test_planner_reflector.py` 覆盖路径纠偏、写后验证收口、日志压缩和反思门禁。

### 9.30 `read_file` 分片读取落在 UTF-8 字符中间会失败

- 现象：读取大文件分片时，如果 `start_index` 正好落在中文等多字节字符中间，工具返回 `DECODE_ERROR`，模型容易继续尝试相近偏移并浪费轮次。
- 修复：仅对 `start_index` 分片读取启用容错解码，完整文件读取仍保持严格解码。
- 验证：`tests/test_tools.py` 覆盖 UTF-8 边界偏移仍可返回可用内容。

### 9.31 session JSON 增加 schema version 和集中迁移层

- 现象：历史会话兼容曾依赖模型 Literal 临时保留旧错误码；如果未来再次出现旧错误码或字段变化，`manus-mini list/resume` 仍可能在 Pydantic 校验前崩溃。
- 修复：
  - `SessionState` 增加 `schema_version=1`。
  - `SessionStore.load()` 和 `list_sessions()` 统一先调用 `migrate_session_data()` 再校验模型。
  - 未知旧错误码迁移为 `UNKNOWN_ERROR`，并在 `metadata.legacy_code` 保留原始错误码。
- 验证：`tests/test_session_store.py` 覆盖保存版本号、旧会话迁移和未知错误码兼容。

### 9.32 等待用户选择导致外层执行到上限

- 现象：用户表达“没想好换啥，反正就得换个”时，Agent 反复输出多组文案选项并等待用户选择；Reflection 持续返回 `local_update`，Runtime 继续下一工程步，最终达到 `max_engineering_steps` 才失败。
- 根因：
  - Planner 把“用户已授权自行选择”的任务规划成“给用户选项并等待选择”。
  - Reflection 没有把“用户已授权自行决定但草稿仍等待用户选择”识别为错误方向。
  - Runtime 遇到真正需要用户选择的 `local_update` 时没有停止等待外部输入，而是继续消耗工程步数。
- 修复：
  - Planner 对“没想好 / 你来定 / 反正 / 随便”等委托选择表达做计划后处理，移除等待用户选择步骤，改为自行选择保守方案并执行。
  - ReAct 提示词明确：用户已授权代选时，不要只给选项等待用户选择。
  - Reflection 对委托选择任务中的“等待用户选择”草稿返回 `regenerate`，要求直接选方案并执行。
  - Runtime 对真正等待用户选择/确认的反思结果直接收口返回当前草稿，不再跑到外层上限。
- 验证：`tests/test_planner_reflector.py` 和 `tests/test_runtime.py` 覆盖 Planner 改写、Reflection 拒绝等待选择草稿、Runtime 等待外部选择时单步收口。

### 9.33 测试误判和碎片读取导致 ReAct / Reflection 反复循环

- 现象：真实运行中 LLM 连续调用工具直到达到循环上限；其中包含大量同一文件的小范围 `read_file(start_index, max_bytes=200)`，以及测试命令 `pytest ... | tail` 输出已经包含失败信息但 shell exit code 仍为 0，ReAct 误判为“测试已通过”并强制最终答复。
- 根因：
  - 写后验证只看 `ok=True` 和 `exit_code=0`，没有检查 stdout/stderr 中的 `FAILED`、`FAILURES`、`Traceback` 等失败标记。
  - 写后验证在最新代码写入后只要曾经见过一次通过的测试，就忽略后续失败工具或被预算拒绝的工具，导致过早收口。
  - `read_file` 去重只按完全相同的 `path/start_index/max_bytes` 指纹判断，不限制同一轮对同一文件发起多个相邻小切片读取。
- 修复：
  - ReAct 写后验证要求最新写入后的工具事件不能出现失败或错误码；测试类工具还必须同时满足 `ok=True`、`exit_code=0` 且输出中没有失败标记。
  - Reflection 测试门禁使用同样的失败输出识别，避免管道吞掉测试失败码时误判 accept。
  - 同一轮同一文件最多允许 2 个分片 `read_file`，更多小切片会返回 `READ_FILE_FRAGMENT_LIMIT_EXCEEDED`，提示改用更大的 `max_bytes` 或更精确的 grep/sed。
- 验证：`tests/test_runtime.py` 覆盖管道输出失败时不强制收口，以及同一轮碎片读取限制；`tests/test_planner_reflector.py` 覆盖 Reflection 对失败输出标记的拒绝。

### 9.34 Reflection 强制接受与代码修改前置测试流程

- 现象：当前阶段希望减少 Reflection 自我否决带来的反复循环，同时要求代码修改任务严格遵循“先写测试用例、先执行测试、再改生产代码”的流程。
- 根因：
  - Reflection 原先会根据 LLM 或规则返回 `local_update/regenerate/replan`，在部分任务中会继续消耗外层工程步数。
  - `replace_in_file` 等写入工具只在写后测试门禁上做收口，没有在写生产代码之前强制要求已经执行过测试。
  - 确认写入后如果同一轮产生新的 pending confirmation，旧逻辑可能清掉新的 pending 状态。
- 修复：
  - `ReflectionLoop.run()` 现在只执行一次 ReAct，然后统一记录并返回 `accept`。
  - `LoopLimits.max_tool_calls_per_iteration` 默认值从 5 提升到 99。
  - ReAct 对 `write_file` / `replace_in_file` / `append_file` 修改生产代码增加前置门禁：必须先执行过测试命令或测试脚本；测试文件本身不拦截。
  - 测试命令允许在代码修改后重复执行，避免复测被重复调用去重拦截。
  - 确认流程只清理当前已确认的 pending，保留续跑过程中产生的新 pending。
- 验证：新增和更新 `tests/test_runtime.py`、`tests/test_models.py`、`tests/test_planner_reflector.py`、`tests/support.py`，覆盖强制 accept、默认 99 次工具调用、代码修改前置测试门禁、确认续跑和写后复测。

### 9.35 高风险命令由 LLM 判定并增加二次确认

- 现象：旧逻辑把“高风险副作用命令 + 工作区外绝对路径”作为二次确认条件，但实际风险不应只由是否影响工作目录外决定。
- 根因：
  - 路径作用域只能说明影响范围，不能完整判断命令是否高风险。
  - 同样是工作区外路径，有些命令可能只是读取或无害检查；工作区内命令也可能具备破坏性。
- 修复：
  - `run_bash` / `run_temp_script` 支持注入命令风险判定器。
  - 默认真实 OpenAI-compatible LLM 路径下，由 LLM 对命令或脚本返回 `high/low` 风险判断。
  - LLM 判为 `high` 时返回 `COMMAND_REQUIRES_CONFIRMATION`，并通过 Executor 进入 pending confirmation。
  - 不再因为命令影响工作区外路径就自动判为高风险。
  - 用户确认后才会带 `confirmed=true` 真正执行；原有 `sudo`、`rm -rf /` 等灾难命令仍直接拒绝。
  - 确认提示对命令类工具使用“即将执行”，避免和文件写入的“即将修改”混淆。
- 验证：`tests/test_shell_tools.py` 覆盖 LLM 风险判定触发确认、外部路径不再自动触发确认、确认后执行；`tests/test_runtime.py` 覆盖 ReAct 在 LLM 标记 high risk 时进入用户二次确认。

### 9.36 TUI 展示 LLM reasoning_content

- 现象：模型返回了 `reasoning_content`，但 TUI 执行过程只显示“LLM 返回 / 已返回”，用户看不到模型为什么要调用这些工具。
- 根因：
  - ReAct trace event 只记录 `content_preview` 和 `tool_calls`，没有把 `reasoning_content` 写入 trace。
  - TUI formatter 对 LLM 回合只输出固定占位行。
- 修复：
  - ReAct 的 LLM trace event 增加 `reasoning_content` 摘要字段。
  - TUI 在 `LLM 回合 N` 下展示 `推理: ...`，多行会压缩为单行，长内容截断并脱敏。
  - 移除固定占位的“LLM 返回”和“- 已返回”。
  - 推理行使用更亮的 `process.reasoning` 样式，和普通过程文本区分。
- 验证：`tests/test_prompt_tui.py` 覆盖 reasoning_content 展示、占位行移除和推理行高亮样式。

### 9.37 TUI 多轮对话展示不再清空

- 现象：每次发起新对话时，TUI 会用当前轮 transcript 重画主输出区，上一轮的用户问题、执行过程和最终产物会从界面上消失。
- 根因：TUI 只根据 `session.active_task` 渲染当前轮，没有把已完成轮次固化为历史展示块。
- 修复：
  - `PromptTui` 增加已完成 transcript 块列表。
  - 当前轮运行时展示为“历史块 + 当前轮”；当前轮完成后，将最终 transcript 固化进历史块。
  - 等待确认的中间状态不固化，避免重复保存未完成轮次。
  - 每轮 transcript 开头增加“会话信息”，展示当前 `Run ID`，方便定位日志和产物。
- 验证：`tests/test_prompt_tui.py` 覆盖新输入时保留上一轮展示、每轮显示 Run ID、最终输出仍可滚动回完整历史。

### 9.38 默认 outputs 目录迁移到项目隔离用户目录

- 现象：`runs`、`sessions`、`memory.db` 已经迁移到 `~/.manus-mini/projects/<project_key>/...`，但默认 `outputs/` 仍会落在当前工作目录或 pytest 的全局临时 outputs 下。
- 根因：`AgentRuntime._default_reporter_output_dir()` 仍返回 `Path("outputs")` 或 pytest 临时全局目录，没有复用项目隔离存储路径。
- 修复：
  - 新增 `project_outputs_dir(cwd)`。
  - 默认 `Reporter.output_dir` 改为 `~/.manus-mini/projects/<project_key>/outputs`。
  - 默认 `Reporter.run_root` 继续对应同一个项目隔离目录下的 `runs`。
  - 显式传入 `Reporter(output_dir)` 时仍尊重调用方路径。
- 验证：`tests/test_logging.py` 覆盖项目隔离 outputs 路径；`tests/test_runtime.py` 覆盖默认 reporter 输出目录和 run summary 根目录。

### 9.39 TUI 欢迎页展示 LLM 配置来源

- 现象：TUI 欢迎页只展示当前模型，不展示配置来自环境变量、当前项目 `.env`、用户 `~/.manus-mini/.env` 还是源码根目录 `.env`。
- 根因：`PromptTui._format_initial_welcome()` 没有把 `AppConfig.llm_config_source` 传给 formatter。
- 修复：
  - TUI 初始化时按当前 `cwd` 读取 `.env`。
  - 欢迎页在配置可用时展示“配置来源”。
- 验证：`tests/test_prompt_tui.py` 覆盖当前项目 `.env` 被识别并展示配置来源。

### 9.40 工具执行时间默认不限制

- 现象：用户希望工具执行时间不做限制，避免长时间测试、构建、安装或分析任务被框架层截断。
- 根因：
  - `LoopLimits.max_tool_timeout_seconds` 默认 30 秒。
  - Executor 调用工具时总是把该值传给 `future.result(timeout=...)`。
  - `run_bash` / `run_temp_script` 默认也会把 30 秒 timeout 传给 `subprocess.run()`。
- 修复：
  - `LoopLimits.max_tool_timeout_seconds` 默认改为 `None`。
  - Executor 将 `None / 0 / 负数` 解释为“不限制”，只有正数才启用外层工具超时。
  - 命令工具默认不传 subprocess timeout；如果调用方显式传入 `timeout_seconds`，仍按指定秒数限制。
  - TUI 欢迎页改为展示“工具执行时间：不限制”。
- 验证：`tests/test_models.py` 覆盖默认值；`tests/test_runtime.py` 覆盖慢工具不会因为 `max_tool_timeout_seconds=0` 被截断；`tests/test_shell_tools.py` 覆盖命令工具默认执行；`tests/test_prompt_tui.py` 覆盖欢迎页文案。

### 9.41 工具执行统一使用最大 8 worker 线程池

- 现象：旧实现中单个工具执行会临时创建 `ThreadPoolExecutor(max_workers=1)`；批量工具执行会按 batch 长度临时创建线程池，batch 较大时可能一次启动过多线程。
- 根因：
  - 工具执行线程池没有集中管理。
  - 批量执行按工具数量直接设置 worker 数，缺少统一并发上限。
- 修复：
  - `Executor` 初始化时创建共享工具线程池，默认最大 worker 数为 8。
  - 单个工具调用和批量工具调用都提交到同一个工具线程池执行。
  - 批量执行不再按 batch 大小临时创建线程池，避免超过 8 个工具并发执行。
  - 为避免同池嵌套等待，批量执行直接提交单个工具执行单元，不再在 worker 内二次提交 `tool.run`。
- 验证：`tests/test_runtime.py` 覆盖 12 个同批工具调用时实际并发数大于 1 且不超过 8；同时保留工具不限时、重试耗尽和 shell 工具回归测试。

### 9.42 放开 overview 任务的深层源码读取限制

- 现象：用户询问项目技术架构时，LLM 根据目录结构有针对性读取 `src/features/...` 下的相关源码文件，但被 `PROJECT_SCOPE_RESTRICTED` 拦截。
- 根因：
  - 早期为了防止概览类任务全仓乱读，对 overview 任务只允许读取 README、项目元数据、docs 文档和少量核心入口文件。
  - 现在 ReAct 开始前已经把当前项目目录结构发给 LLM，模型能更有针对性地选择文件；继续用固定白名单会误伤合理读取。
- 修复：
  - 移除 overview 任务的 `read_file` 白名单限制。
  - 不再返回 `PROJECT_SCOPE_RESTRICTED`。
  - 继续保留工具预算、重复读取去重、分片读取限制、路径安全保护等通用约束，避免失控读取。
- 验证：`tests/test_runtime.py` 覆盖 overview 任务可以读取有针对性的深层源码文件；同时保留单轮多文件读取和重复读取去重回归。

### 9.43 日志从 run 级目录改为 session 级目录

- 现象：同一个会话的多轮对话已经保存在同一个 `SessionState.messages` 中，但运行日志仍按 `runs/<session_id>-<run_id>/` 拆分，排查同一会话上下文时需要跨多个目录查找。
- 根因：早期日志模型以单轮任务为中心，`EventLogger` 的落盘路径绑定 `run_id`；Reporter 的 run summary 也按每轮 run 单独建目录。
- 修复：
  - 默认日志目录改为 `~/.manus-mini/projects/<project_key>/logs`。
  - 每个 session 日志目录固定为 3 个文件：`summary.jsonl`、`pipeline.jsonl`、`node.jsonl`。
  - `summary.jsonl` 只记录每轮用户输入、执行状态、执行结果和最终 `final_node_id`，用于快速判断对话质量。
  - `pipeline.jsonl` 记录每轮从开始到结束涉及的节点、节点状态、关联消息 ID、`node_id` 和 `upstream_node_ids`，用于定位失败节点。
  - `node.jsonl` 记录每个节点的输入输出详情，包括用户输入、LLM 请求/响应、审查节点、工具输入输出等；可通过 `run_id + node_id` 查询具体节点，并沿 `upstream_node_ids` 追溯上游节点。
  - Reporter 不再向日志目录写每轮 Markdown summary，避免 session 日志目录超过 3 个文件。
  - 删除/清空 session 时同步清理对应 session 级日志目录。
- 验证：`tests/test_logging.py` 覆盖三文件结构、不同 run 追加到同一 session、`node_id` 与上游链路；`tests/test_runtime.py` 和 `tests/test_session_store.py` 覆盖默认路径、summary 到 pipeline/node 的关联、节点详情和日志清理。

## 10. 近期关键提交快照

本节记录 2026-07-02 近期已经提交的关键能力，避免继续沿用“当前未提交 diff”的旧口径。

### 10.1 `0ce4fce feat: add command tools and test gate`

- 新增 `run_bash` 和 `run_temp_script`，支持受控 bash 命令执行和临时脚本执行后自动删除。
- LLM tool schema 暴露 bash / 临时脚本工具参数。
- 代码修改类任务增加测试门禁：未执行测试不接受，测试失败回传失败信息，最近测试通过才允许 Reflection 接受。
- 更新中文问题与优化记录，并将验证基线推进到 `237 passed`。

### 10.2 `e1a15b5 docs: add agent behavior guidelines`

- 新增项目根目录 `AGENTS.md`。
- 明确项目文档默认中文。
- 新增或修改英文文档时，必须同步新增或修改对应中文文档。
- 如果中英文双版本同时存在，后续修改必须同步更新两边。

### 10.3 `4a18a75 feat: add precise file replacement tool`

- 新增 `replace_in_file`，用于已有文件的 `old_text -> new_text` 精确局部替换。
- 默认只允许匹配 1 处，多处匹配需要显式传入 `expected_replacements`。
- 找不到旧文本返回 `OLD_TEXT_NOT_FOUND`，替换次数不符合预期返回 `REPLACEMENT_COUNT_MISMATCH`。
- 等长替换走原地写入；长度变化走同目录临时文件 + `os.replace` 原子替换。
- `replace_in_file` 不需要人工确认，依赖精确旧文本匹配、替换次数校验和 workspace/protected path 保护。
- 执行提示已要求修改已有文件优先使用 `replace_in_file`，只有创建新文件或必须整体重写时才使用 `write_file`。
- 更新中文问题与优化记录，并将验证基线推进到 `244 passed`。

## 11. 当前验证基线

最近一次完整验证：

```bash
python -m pytest -q
# 279 passed
```

补充手工验证：

```bash
LLM_PROVIDER=openai-compatible LLM_BASE_URL=http://localhost:1234/v1 LLM_API_KEY=your-api-key python - <<'PY'
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

## 12. V2 优化方向

V2 不再优先堆更多工具，而是先提升真实长任务的稳定性、可恢复性和成本可控性。

### 12.1 执行效率

- 建立文件定位索引：对源码、测试、文档维护轻量索引，LLM 请求不存在路径时优先从索引纠偏，减少 `list_files` / `grep` 循环。
- 增强工具结果复用：同一 run 内对等价读取、扫描、测试结果建立摘要缓存，下一轮优先引用缓存摘要。
- 增加收口判定：当任务已修改代码并通过相关测试，或问题分析已覆盖关键证据时，ReAct 主动要求最终答复。

### 12.2 质量门禁

- 代码任务按语言和文件类型推断默认验证命令，例如 Python 项目优先 `pytest`，前端项目优先 `pnpm test` / `pnpm lint`。
- 区分“语法检查”“lint”“单测”“集成测试”，Reflection 不把语法检查误判成完整测试。
- 对失败测试生成结构化摘要，下一轮优先修失败点，不重新扫描无关文件。

### 12.3 日志与可观测性

- JSONL 主日志保持轻量，只保留决策、摘要、耗时和错误码；完整大 payload 后续可落到按需 sidecar 文件。
- 为每个 run 输出自动复盘摘要：总耗时、LLM 轮次、工具次数、失败工具、重复调用、最终验证命令。
- TUI 增加运行诊断视图，快速看到“为什么继续跑 / 为什么收口 / 当前阻塞点”。

### 12.4 TUI 体验

- diff 展示继续增强：支持文件名分组、hunk 折叠、当前修改摘要和测试结果并列展示。
- 状态栏保持短文本，但提供可展开的运行详情，避免把阶段、动作、最新动态全部挤在一行。
- 对长输出默认折叠，只突出错误、测试结论和用户需要决策的内容。

### 12.5 会话兼容与迁移

- 已落地基础版：session JSON 已有显式 schema version，`SessionStore` 已集中处理历史错误码迁移。
- 后续继续补 schema version 升级策略，例如 v1 -> v2 的字段重命名、字段拆分和批量离线迁移。
- 继续增加旧版本 session fixture，覆盖更多真实历史 JSON。
