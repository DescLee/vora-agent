# Codex 测试与问题修复记录（二）

本文件从第 101 条开始记录。第 1-100 条见 [codex-testing-and-fixes.zh.md](/Users/liyong/Desktop/ai-manus/docs/codex-testing-and-fixes.zh.md)。

## 问题与修复

### 101. 长期记忆追问在 LLM 不可用时缺少工程细节

#### 现象

- 亲自执行 `python -m vora run "长期记忆是怎么工作的？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时这类问题需要讲清存储、检索、路径和敏感信息过滤。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加长期记忆类直接规则兜底。
- 回答说明 SQLite 存储、`project_memory_path`、关键词检索，以及写入前过滤敏感信息。

#### 回归点

- 模型不可用时，长期记忆追问不得展示 `兜底原因`。
- 回答必须包含 `SQLite`、`关键词检索`、`敏感信息`、`project_memory_path`。

### 102. 搜索失败追问在 LLM 不可用时缺少失败策略说明

#### 现象

- 亲自执行 `python -m vora run "如果搜索失败怎么办？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明不会编造结果，以及无搜索结果、网页读取失败、证据不足时的处理方式。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加搜索失败类直接规则兜底。
- 回答明确“未获取到有效搜索结果”、“页面内容读取失败”和“证据不足”，并建议换关键词或补充资料。

#### 回归点

- 模型不可用时，搜索失败追问不得展示 `兜底原因`。
- 回答必须包含失败原因和证据不足说明。

### 103. 查看历史会话追问在 LLM 不可用时缺少 CLI 指引

#### 现象

- 亲自执行 `python -m vora run "怎么查看历史会话？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 用户无法从兜底回答里知道应该先 `list` 再 `resume`。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加历史会话查看类直接规则兜底。
- 回答说明 `vora list --cwd .` 查看 `session_id`、更新时间、消息数和最近用户问题，再用 `vora resume <session_id> --cwd .` 继续。

#### 回归点

- 模型不可用时，历史会话追问不得展示 `兜底原因`。
- 回答必须包含 `vora list`、`vora resume`、`session_id`。

### 104. dry-run 追问在 LLM 不可用时缺少安全预演说明

#### 现象

- 亲自执行 `python -m vora run "dry-run 是怎么工作的？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明 dry-run 如何避免副作用，而不是只知道有一个参数。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加 dry-run 类直接规则兜底。
- 回答说明 `--dry-run` 会返回确认预览和计划动作，但不落盘、不真正执行有副作用操作。

#### 回归点

- 模型不可用时，dry-run 追问不得展示 `兜底原因`。
- 回答必须包含 `--dry-run`、不落盘和确认预览。

### 105. 命令风险追问在 LLM 不可用时缺少工具和确认机制说明

#### 现象

- 亲自执行 `python -m vora run "命令执行怎么控制风险？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要能讲清命令工具、风险判断、拒绝和确认路径。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加命令风险类直接规则兜底。
- 回答说明 `run_bash`、`run_temp_script`、命令风险判断、危险命令拒绝或确认流程。

#### 回归点

- 模型不可用时，命令风险追问不得展示 `兜底原因`。
- 回答必须包含 `run_bash`、`run_temp_script`、命令风险和确认。

### 106. 工具并行调度追问在 LLM 不可用时缺少调度策略说明

#### 现象

- 亲自执行 `python -m vora run "工具并行调度是怎么做的？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要讲清哪些工具能并行、哪些必须串行，以及为什么。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加工具调度类直接规则兜底。
- 回答说明 `ToolScheduler` 会并行无依赖只读工具，写入工具、敏感工具和有依赖工具保持串行。

#### 回归点

- 模型不可用时，工具调度追问不得展示 `兜底原因`。
- 回答必须包含 `ToolScheduler`、只读工具、并行和写入工具。

### 107. Reflection 质量门禁追问被泛化成测试命令清单

#### 现象

- 亲自执行 `python -m vora run "Reflection 质量门禁怎么设计的？" --cwd <目录> --max-steps 1 --max-react 1` 时，返回的是泛化测试命令清单。
- 这不是网络错误兜底，但没有回答 Reflection 的位置、决策和 pytest gate 机制。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加 Reflection 质量门禁类直接规则兜底，并放在泛化测试命令规则之前。
- 回答说明 Reflection 位于 ReAct 草稿之后，代码任务检查测试证据，必要时运行 pytest gate，决策包括 `accept`、`local_update`、`regenerate`、`replan`。

#### 回归点

- Reflection 追问不得退化成单纯测试命令清单。
- 回答必须包含 `Reflection`、`pytest gate`、`accept`、`replan`。

### 108. eval 追问在 LLM 不可用时缺少评测范围说明

#### 现象

- 亲自执行 `python -m vora run "eval 是怎么跑的？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明 eval 命令和覆盖的产品约束，而不是只说跑测试。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加 eval 类直接规则兜底。
- 回答说明 `python evals/run_evals.py` 当前覆盖 12 个关键约束，包括 Reflection、tool exchange、并行调度、写入确认和安全边界。

#### 回归点

- 模型不可用时，eval 追问不得展示 `兜底原因`。
- 回答必须包含 eval 命令、12 个约束、Reflection 和安全边界。

### 109. 架构讲法追问在 LLM 不可用时缺少主链路说明

#### 现象

- 亲自执行 `python -m vora run "这个项目的架构怎么讲？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要用一条清晰主链路讲模块边界。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加架构讲法类直接规则兜底。
- 回答按 `Runtime -> Planner -> ReAct -> ToolScheduler -> Executor/Observer -> Reflection -> Reporter` 说明模块职责。

#### 回归点

- 模型不可用时，架构讲法追问不得展示 `兜底原因`。
- 回答必须包含 `Runtime`、`Planner`、`ReAct`、`ToolScheduler`、`Reflection`。

### 110. 打包发布追问在 LLM 不可用时缺少构建产物说明

#### 现象

- 亲自执行 `python -m vora run "怎么打包发布？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明 Python 包构建入口、产物和提交前门禁。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加打包发布类直接规则兜底。
- 回答说明 `pyproject.toml`、`python -m build`、sdist、wheel、`dist/` 和提交前测试门禁。

#### 回归点

- 模型不可用时，打包发布追问不得展示 `兜底原因`。
- 回答必须包含 `python -m build`、`sdist`、`wheel`、`pyproject.toml`。

### 111. 项目边界与不足追问在 LLM 不可用时缺少诚实边界说明

#### 现象

- 亲自执行 `python -m vora run "这个项目有哪些边界和不足？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时这类问题需要主动讲清本地单用户、非生产级、非容器沙箱、非向量检索等边界。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加项目边界类直接规则兜底。
- 回答说明本地单用户、非生产级、命令执行不是容器沙箱、memory 不是向量检索，以及 LLM adapter 仍缺 streaming 和多 provider。

#### 回归点

- 模型不可用时，项目边界追问不得展示 `兜底原因`。
- 回答必须包含本地单用户、非生产级、向量检索和容器沙箱等关键词。

### 112. 与真正 Manus 的差距追问在 LLM 不可用时缺少定位说明

#### 现象

- 亲自执行 `python -m vora run "和真正的 Manus 比差在哪里？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要避免“复刻完整 Manus”的错觉，明确这是本地 Agent Runtime 工程骨架。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加 Manus 差距类直接规则兜底。
- 回答说明它不是完整 Manus，差距在浏览器自动化、远程沙箱、多租户、任务市场、长周期任务编排和云端可观测平台。

#### 回归点

- 模型不可用时，Manus 对比追问不得展示 `兜底原因`。
- 回答必须包含不是完整 Manus、浏览器自动化、多租户和远程沙箱。

### 113. 面试演示追问在 LLM 不可用时缺少演示路线

#### 现象

- 亲自执行 `python -m vora run "面试时应该怎么演示这个项目？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 用户无法从兜底回答里得到一条可现场展示的路径。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加面试演示类直接规则兜底。
- 回答建议按项目分析、写入确认、Reflection/pytest gate 和会话恢复三段展示。

#### 回归点

- 模型不可用时，面试演示追问不得展示 `兜底原因`。
- 回答必须包含项目分析、写入确认、Reflection 和会话恢复。

### 114. 扩展新工具追问在 LLM 不可用时缺少扩展接口说明

#### 现象

- 亲自执行 `python -m vora run "怎么扩展一个新工具？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要能说明工具协议、注册和测试，而不是只说“加代码”。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加工具扩展类直接规则兜底。
- 回答说明新增工具类、定义 `ToolSpec`、返回 `ToolResult`、注册到 `ToolRegistry`，并补工具、调度和风险测试。

#### 回归点

- 模型不可用时，工具扩展追问不得展示 `兜底原因`。
- 回答必须包含 `ToolSpec`、`ToolRegistry`、`ToolResult` 和测试。

### 115. 生产化缺口追问在 LLM 不可用时缺少取舍说明

#### 现象

- 亲自执行 `python -m vora run "这个项目生产化还缺什么？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要讲清从 MVP 到生产系统还缺哪些工程能力。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加生产化缺口类直接规则兜底。
- 回答说明多租户、容器隔离、权限审计、集中可观测性、配额、密钥托管、任务队列和 SLA。

#### 回归点

- 模型不可用时，生产化追问不得展示 `兜底原因`。
- 回答必须包含多租户、容器隔离、权限审计和可观测性。

### 116. 排障追问在 LLM 不可用时缺少定位链路

#### 现象

- 亲自执行 `python -m vora run "出问题时怎么排障？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明从 session 到日志、trace 和 summary 的排障路径。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加排障类直接规则兜底。
- 回答说明先拿 `session_id`，再查 `logs`、会话 JSON、`trace_events` 和 summary，并沿 LLM 请求、tool call、工具返回、Reflection 决策、最终报告定位。

#### 回归点

- 模型不可用时，排障追问不得展示 `兜底原因`。
- 回答必须包含 `session_id`、`logs`、`trace_events` 和 summary。

### 117. 资深度追问在 LLM 不可用时缺少工程成熟度表达

#### 现象

- 亲自执行 `python -m vora run "为什么说这是一个8年经验水平的项目？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时这类问题需要把项目价值落到边界、安全、可观测、测试等工程成熟度上。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加资深度类直接规则兜底。
- 回答说明它体现的是安全边界、可观测日志、会话恢复、上下文压缩、工具调度、Reflection 质量门禁和测试/eval 门禁。

#### 回归点

- 模型不可用时，资深度追问不得展示 `兜底原因`。
- 回答必须包含边界、安全、可观测和测试。

### 118. TUI 首屏对默认入口和安全行为说明不够直接

#### 现象

- 用户预期 `vora` 直接进入 TUI，`vora tui` 不再作为入口。
- 旧欢迎页只写“当前界面：vora”，缺少 `list`、默认存储位置、写入确认和 dry-run 等实际使用信息。

#### 修复

- 在 [src/vora/prompt_tui_formatting.py](/Users/liyong/Desktop/ai-manus/src/vora/prompt_tui_formatting.py) 中优化 TUI 欢迎页。
- 首屏明确默认 TUI 入口是 `vora --cwd .`，补充 `vora list --cwd .`、`resume`、写入前 diff 确认、dry-run 和 `~/.vora/projects/<project_key>` 存储位置。
- 在 [src/vora/cli.py](/Users/liyong/Desktop/ai-manus/src/vora/cli.py) 的根帮助里补充 `Interactive mode: vora --cwd .`，但不把 `tui` 作为命令或概念重新暴露。
- 保持首屏不展示 `vora tui`，避免用户继续学习已删除入口。

#### 回归点

- `vora` 无子命令必须进入 TUI。
- `vora tui` 必须继续作为非法子命令被拒绝。
- TUI 欢迎页必须展示默认入口、历史会话查看、写入确认和项目隔离存储位置。

### 119. 核心难点追问在 LLM 不可用时缺少工程抓手

#### 现象

- 亲自执行 `python -m vora run "如果面试官问这个项目的核心难点是什么，怎么回答？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试答辩时需要把难点落到工具调用闭环、上下文、安全和验证，而不是泛泛说“做了 Agent”。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加核心难点类直接规则兜底。
- 回答说明难点是工具调用闭环、上下文预算、安全边界和结果验证，并补充可调度、可确认、可回放和测试证据。

#### 回归点

- 模型不可用时，核心难点追问不得展示 `兜底原因`。
- 回答必须包含工具调用闭环、上下文、安全和验证。

### 120. 可观测性追问在 LLM 不可用时缺少日志链路说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么体现可观测性？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要能说明如何回看每轮请求、计划、工具调用和 Reflection 决策。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加可观测性类直接规则兜底。
- 回答说明 `EventLogger`、`trace_events`、run `summary` 和项目级 `logs`。

#### 回归点

- 模型不可用时，可观测性追问不得展示 `兜底原因`。
- 回答必须包含 `EventLogger`、`trace_events`、`summary` 和 `logs`。

### 121. 为什么不用 LangChain 追问在 LLM 不可用时缺少取舍表达

#### 现象

- 亲自执行 `python -m vora run "面试官问为什么不用 LangChain，怎么回答？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时这类问题不是框架偏好题，而是要说明项目为了展示底层 Agent 工程可控性。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加 LangChain 取舍类直接规则兜底。
- 回答说明不用框架是为了展示工具调度、确认流、上下文压缩、安全边界和 Reflection 回流等底层机制。

#### 回归点

- 模型不可用时，LangChain 取舍追问不得展示 `兜底原因`。
- 回答必须包含可控性、工具调度、确认流和 Agent 工程。

### 122. 上下文窗口溢出追问在 LLM 不可用时缺少压缩策略说明

#### 现象

- 亲自执行 `python -m vora run "上下文窗口爆了怎么办？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明分层压缩和 tool exchange 完整性，而不是只说“截断”。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加上下文窗口溢出类直接规则兜底。
- 回答说明 50% / 70% / 90% 分层处理、`CompressionSnapshot` 和 assistant/tool call/result 成组完整。

#### 回归点

- 模型不可用时，上下文窗口追问不得展示 `兜底原因`。
- 回答必须包含 50%、70%、90% 和 `CompressionSnapshot`。

### 123. 测试质量追问在 LLM 不可用时缺少多层门禁说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么保证测试质量？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要能讲清单测、lint、类型、eval 和覆盖率的分工。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加测试质量类直接规则兜底。
- 回答说明 `pytest`、`ruff`、`mypy`、`python evals/run_evals.py` 和覆盖率门禁。

#### 回归点

- 模型不可用时，测试质量追问不得展示 `兜底原因`。
- 回答必须包含 `pytest`、`ruff`、`mypy` 和 `eval`。

### 124. 工具调用失败追问在 LLM 不可用时缺少错误模型说明

#### 现象

- 亲自执行 `python -m vora run "如果工具调用失败会怎么处理？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明失败如何进入 observation、如何重试，以及如何交给后续回流判断。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加工具失败类直接规则兜底。
- 回答说明失败会转成 `ToolObservation`，保留 `error_code`、summary 和错误说明，可重试错误走重试上限，不可恢复错误交给 ReAct/Reflection。

#### 回归点

- 模型不可用时，工具失败追问不得展示 `兜底原因`。
- 回答必须包含 `ToolObservation`、`error_code`、重试和 Reflection。

### 125. 幻觉控制追问在 LLM 不可用时缺少证据约束说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么避免幻觉？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要强调工具结果、文件内容、搜索结果和证据不足时不编造。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加幻觉控制类直接规则兜底。
- 回答说明回答应基于真实 observation、文件内容和搜索结果；证据不足时明确说明不会编造；代码类结果还经过 Reflection 和测试证据检查。

#### 回归点

- 模型不可用时，幻觉控制追问不得展示 `兜底原因`。
- 回答必须包含工具结果、证据、Reflection 和不会编造。

### 126. 下一版迭代追问在 LLM 不可用时缺少优先级判断

#### 现象

- 亲自执行 `python -m vora run "如果让我继续迭代下一版，你会先做什么？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要体现产品和工程优先级，而不是随口罗列功能。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加下一版迭代类直接规则兜底。
- 回答优先选择 streaming 输出、容器沙箱、多 provider LLM adapter 和更完整的可观测运行详情视图。

#### 回归点

- 模型不可用时，下一版迭代追问不得展示 `兜底原因`。
- 回答必须包含 streaming、容器沙箱、多 provider 和可观测。

### 127. 并发和状态一致性追问在 LLM 不可用时缺少状态模型说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么处理并发和状态一致性？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要主动说明当前是本地单用户 runtime，以及如何避免工具并发写入和 session 状态错乱。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加并发和状态一致性类直接规则兜底。
- 回答说明本地单用户定位、单 session 状态机、`ToolScheduler` 写入/敏感工具串行写入、只读工具并行，以及每轮保存 session 和日志。

#### 回归点

- 模型不可用时，并发和状态一致性追问不得展示 `兜底原因`。
- 回答必须包含单用户、串行写入、`ToolScheduler` 和 session。

### 128. 配置管理和环境隔离追问在 LLM 不可用时缺少配置顺序说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么做配置管理和环境隔离？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明配置读取顺序、项目级数据隔离和运行参数覆盖方式。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加配置管理和环境隔离类直接规则兜底。
- 回答说明环境变量、当前目录 `.env`、`~/.vora/.env`、源码根目录 `.env` 的读取顺序，以及项目级数据按 cwd 映射隔离。

#### 回归点

- 模型不可用时，配置管理追问不得展示 `兜底原因`。
- 回答必须包含 `.env`、环境变量、源码根目录和项目级。

### 129. session 文件损坏追问在 LLM 不可用时缺少兼容策略说明

#### 现象

- 亲自执行 `python -m vora run "如果 session 文件损坏了怎么办？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明损坏会话不会拖垮列表和恢复流程。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加损坏 session 类直接规则兜底。
- 回答说明读取时转成 `CorruptSessionError`，`vora list` 跳过坏文件，`vora resume` 输出友好错误且不暴露 JSON traceback。

#### 回归点

- 模型不可用时，session 损坏追问不得展示 `兜底原因`。
- 回答必须包含 `CorruptSessionError`、`list`、`resume` 和友好错误。

### 130. 数据隔离追问在 LLM 不可用时缺少项目隔离路径说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么做数据隔离？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要能说清不同项目的数据如何不互相污染。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加数据隔离类直接规则兜底。
- 回答说明通过 cwd 计算 `project_key`，默认落到 `~/.vora/projects/<project_key>`，每个项目独立保存 `sessions`、`logs`、`outputs` 和 memory。

#### 回归点

- 模型不可用时，数据隔离追问不得展示 `兜底原因`。
- 回答必须包含 `project_key`、`~/.vora/projects`、`sessions` 和 `logs`。

### 131. 命令超时追问命中泛命令风险回答

#### 现象

- 亲自执行 `python -m vora run "如果命令执行超时怎么办？" --cwd <目录> --max-steps 1 --max-react 1` 时，虽然不展示兜底原因，但只回答命令风险判断。
- 面试时需要说明超时上限、错误码和后续 ReAct/Reflection 处理。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加命令超时类直接规则兜底，并让它先于泛命令风险匹配。
- 回答说明 `max_tool_timeout_seconds`、失败 `ToolObservation`、`TIMEOUT` 类 `error_code` 和失败说明。

#### 回归点

- 命令超时追问必须命中超时专用回答。
- 回答必须包含 `max_tool_timeout_seconds`、`TIMEOUT`、`ToolObservation` 和失败说明。

### 132. 敏感信息和密钥追问在 LLM 不可用时缺少脱敏链路说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么处理敏感信息和密钥？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明日志、TUI、报告、列表展示和长期记忆的敏感信息处理。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加敏感信息和密钥类直接规则兜底。
- 回答说明 `redact_sensitive_text` 在日志、TUI、报告和列表展示前脱敏，覆盖 API key、token、Authorization 等模式，长期记忆写入前也会过滤密钥。

#### 回归点

- 模型不可用时，敏感信息追问不得展示 `兜底原因`。
- 回答必须包含 `redact_sensitive_text`、API key、token 和长期记忆。

### 133. LLM 空结果追问在 LLM 不可用时缺少空响应兜底说明

#### 现象

- 亲自执行 `python -m vora run "如果 LLM 返回空结果怎么办？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明空消息不会被当作成功产物直接交给用户。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加 LLM 空结果类直接规则兜底。
- 回答说明空结果会走 fallback 兜底生成可读回答，并把任务收敛到 `done`，避免 CLI/TUI 展示空白。

#### 回归点

- 模型不可用时，LLM 空结果追问不得展示 `兜底原因`。
- 回答必须包含空结果、fallback、兜底和 `done`。

### 134. 错误分级追问在 LLM 不可用时缺少 error_code 体系说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么做错误分级？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要能举出结构化错误码，而不是只说“有异常处理”。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加错误分级类直接规则兜底。
- 回答说明 `error_code` 体系：`PATH_OUT_OF_WORKSPACE`、`RISK_REJECTED`、`TIMEOUT` 和 `CorruptSessionError`。

#### 回归点

- 模型不可用时，错误分级追问不得展示 `兜底原因`。
- 回答必须包含 `error_code`、`PATH_OUT_OF_WORKSPACE`、`RISK_REJECTED` 和 `TIMEOUT`。

### 135. 项目定位被误写成“面试项目”

#### 现象

- README、产品设计和 ADR 中存在“面向面试展示”“高级工程师面试项目”等表达。
- 这会把项目定位误导成专门包装出来的面试 demo，而不是用户拿去面试讲解的真实本地 Agent Runtime 项目。

#### 修复

- 在 [README.md](/Users/liyong/Desktop/ai-manus/README.md) 中把定位改为“本地 Agent Runtime”，说明它可以用于面试中讲解 Agent 工程能力，而不是项目本身就是面试项目。
- 在 [docs/v1-product-design.md](/Users/liyong/Desktop/ai-manus/docs/v1-product-design.md)、[docs/demo-scenarios.zh.md](/Users/liyong/Desktop/ai-manus/docs/demo-scenarios.zh.md)、[docs/adr/0001-self-managed-agent-runtime.zh.md](/Users/liyong/Desktop/ai-manus/docs/adr/0001-self-managed-agent-runtime.zh.md) 和 [docs/fixed-issues-and-optimizations.md](/Users/liyong/Desktop/ai-manus/docs/fixed-issues-and-optimizations.md) 中同步调整定位表述。
- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中把“为了面试展示底层可控性”等回答改为“把底层可控性讲清楚”，避免 Agent 自述时贬低项目定位。

#### 回归点

- 项目主文档不得再把 vora 定义成“面试项目”或“面向面试展示”的项目。
- 面试相关内容只作为项目讲解场景存在，不作为产品定位。

### 136. 任务取消和中断恢复追问在 LLM 不可用时缺少恢复路径

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么做任务取消和中断恢复？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 工程评审时需要说明 TUI 退出、session 保存和 resume 恢复之间的关系。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加任务取消和中断恢复类直接规则兜底。
- 回答说明 `Ctrl+C` 退出交互后，已保存的 session、日志和上下文仍可通过 `vora list` / `vora resume` 恢复排查或继续。

#### 回归点

- 模型不可用时，任务取消和中断恢复追问不得展示 `兜底原因`。
- 回答必须包含 `Ctrl+C`、session、resume 和日志。

### 137. 日志隐私追问命中日志位置回答

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么保证日志不会泄露隐私？" --cwd <目录> --max-steps 1 --max-react 1` 时，回答只说明 `logs` 保存位置，没有说明脱敏链路。
- 这类问题需要回答落盘前脱敏，而不是只告诉用户日志在哪。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加日志隐私类直接规则兜底，并让它先于泛日志位置匹配。
- 回答说明 `redact_sensitive_text`、`logs` 落盘前压缩脱敏、token/API key/Authorization 替换，以及 TUI/报告/列表展示复用脱敏逻辑。

#### 回归点

- 日志隐私追问必须命中脱敏专用回答。
- 回答必须包含 `redact_sensitive_text`、`logs`、token 和落盘前。

### 138. 工具参数校验追问在 LLM 不可用时缺少 schema 说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么处理工具参数校验？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 工程评审时需要说明工具 schema、本地校验、失败结果和错误码。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加工具参数校验类直接规则兜底。
- 回答说明 `ToolSpec` 的 JSON schema、本地校验、`ToolResult` 失败结果和 `INVALID_ARGUMENTS`。

#### 回归点

- 模型不可用时，工具参数校验追问不得展示 `兜底原因`。
- 回答必须包含 `ToolSpec`、schema、`INVALID_ARGUMENTS` 和 `ToolResult`。

### 139. 工具权限范围追问命中泛安全回答

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么限制工具权限范围？" --cwd <目录> --max-steps 1 --max-react 1` 时，只回答泛安全边界。
- 这类问题需要说明工具元数据和执行层权限校验。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加工具权限范围类直接规则兜底，并让它先于泛安全匹配。
- 回答说明 `ToolSpec` 描述 schema、risk 和是否需要确认，执行层还会校验 cwd 边界，高风险工具拒绝或进入确认流程。

#### 回归点

- 工具权限范围追问必须命中权限范围专用回答。
- 回答必须包含 `ToolSpec`、risk、cwd 和确认。

### 140. 审计追踪追问在 LLM 不可用时缺少链路说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么做审计追踪？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 工程评审时需要说明如何串起请求、计划、工具、确认和 Reflection 决策。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加审计追踪类直接规则兜底。
- 回答说明 `trace_events`、`EventLogger`、`run_id` 和 run `summary`。

#### 回归点

- 模型不可用时，审计追踪追问不得展示 `兜底原因`。
- 回答必须包含 `trace_events`、`EventLogger`、`run_id` 和 `summary`。

### 141. 重复读取和上下文浪费追问在 LLM 不可用时缺少 dedupe 说明

#### 现象

- 亲自执行 `python -m vora run "这个项目如何避免重复读取和上下文浪费？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 工程评审时需要说明同轮 read_file 去重和跨轮上下文压缩。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加重复读取和上下文浪费类直接规则兜底。
- 回答说明同轮 `read_file` dedupe、重复请求记录 skipped trace，以及跨轮依赖上下文压缩和摘要。

#### 回归点

- 模型不可用时，重复读取追问不得展示 `兜底原因`。
- 回答必须包含重复读取、dedupe、`read_file` 和上下文。

### 142. 长输出和报告落盘追问在 LLM 不可用时缺少产物策略说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么处理长输出和报告落盘？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 工程评审时需要说明长报告如何落盘、如何避免直接挤爆上下文。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加长输出和报告落盘类直接规则兜底。
- 回答说明长输出优先生成 Markdown 产物落到 `outputs`，运行摘要记录路径，进入上下文前做摘要或截断。

#### 回归点

- 模型不可用时，长输出追问不得展示 `兜底原因`。
- 回答必须包含 `outputs`、Markdown、摘要和上下文。

### 143. 多轮对话目标漂移追问在 LLM 不可用时缺少状态回流说明

#### 现象

- 亲自执行 `python -m vora run "这个项目怎么处理多轮对话里的目标漂移？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 工程评审时需要说明 session、active_task、Planner 和 Reflection 如何共同控制目标漂移。

#### 修复

- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中增加目标漂移类直接规则兜底。
- 回答说明同一 session 维护历史和 `active_task`，新输入交给 Planner 重新判断目标，Reflection 检查是否偏离，必要时 replan 或 regenerate。

#### 回归点

- 模型不可用时，目标漂移追问不得展示 `兜底原因`。
- 回答必须包含 session、`active_task`、Planner 和 Reflection。

### 144. 评测体系缺少面试里最容易被追问的工程边界回归

#### 现象

- 现有 `evals/run_evals.py` 只覆盖 9 个基础约束，缺少报告默认不落文件、待确认状态隔离和 `Path.write_bytes` 代码门禁这类高频追问的回归。
- 这会让 eval 报告更像基础单测汇总，而不是可直接展示的工程约束回归集。

#### 修复

- 在 [evals/cases.zh.json](/Users/liyong/Desktop/ai-manus/evals/cases.zh.json) 中新增 3 个 eval case，分别覆盖：
  - 报告类请求默认不能通过 shell 旁路落文件
  - 待确认写入期间，普通消息不能开启新任务
  - shell 里的 `Path.write_bytes` 生产代码写入必须先有测试证据
- 在 [evals/run_evals.py](/Users/liyong/Desktop/ai-manus/evals/run_evals.py) 中补充分类统计，方便面试时直接展示 eval 结构。

#### 回归点

- `python evals/run_evals.py` 必须返回 12/12 通过。
- markdown 报告中应同时展示用例明细和分类统计。

### 145. 面试演示前缺少本地自检入口

#### 现象

- 演示前需要确认当前工作目录、项目隔离存储路径、会话目录、日志目录、memory DB 和 LLM 配置是否就绪。
- 旧流程只能分别看 README、`.env`、`list` 输出和运行时错误，现场排障路径分散。

#### 修复

- 在 [src/vora/cli.py](/Users/liyong/Desktop/ai-manus/src/vora/cli.py) 中新增 `vora doctor --cwd .`。
- 该命令只检查本地配置和存储路径，不主动请求 LLM API，避免自检依赖网络或模型服务。
- 输出包括项目存储目录、sessions/logs/outputs/memory 路径、已保存会话数、LLM provider/base URL/API key 是否配置、模型名和下一步命令。
- 在 [README.md](/Users/liyong/Desktop/ai-manus/README.md) 中补充启动自检能力和运行示例。

#### 回归点

- `vora doctor --cwd <目录>` 应能输出本地诊断信息。
- `doctor` 不应泄露 `LLM_API_KEY` 原始值。
- 顶层 help 和 `doctor --help` 应展示自检入口与“不调用 LLM API”的边界。

### 146. Agent 缺少可扩展的 Skills 能力层

#### 现象

- 旧实现只有通用 Planner/ReAct prompt 和工具注册表，任务流程约束主要写死在运行时提示词里。
- 用户如果想新增“项目分析”“代码修改”“面试演示”这类可复用能力，只能改业务代码或继续堆 prompt，不利于面试时说明扩展边界。
- 工具权限只能按全局工具列表暴露给模型，缺少按任务能力收窄工具候选范围的机制。

#### 修复

- 新增 [src/vora/skills/](/Users/liyong/Desktop/ai-manus/src/vora/skills)：
  - `SkillSpec` 描述 `name/description/triggers/instructions/tool_allowlist/acceptance`。
  - loader 支持从 `skill.json` + `instructions.md` 加载本地 Skill，坏 Skill 自动跳过。
  - registry 支持内置 Skill、项目级 `./skills/*` 和用户级 `~/.vora/skills/*`。
- 在 [src/vora/runtime.py](/Users/liyong/Desktop/ai-manus/src/vora/runtime.py) 中根据用户输入匹配 Skill，并写入 `task.metadata["active_skill"]`。
- 在 [src/vora/planner.py](/Users/liyong/Desktop/ai-manus/src/vora/planner.py) 中把 active Skill 注入 Planner prompt，使计划阶段能遵循能力包约束。
- 在 [src/vora/react.py](/Users/liyong/Desktop/ai-manus/src/vora/react.py) 中把 active Skill 注入执行 prompt，并按 `tool_allowlist` 过滤传给 LLM 的工具候选。
- 在 [README.md](/Users/liyong/Desktop/ai-manus/README.md) 中补充用户新增 Skill 的目录结构和示例。

#### 回归点

- 本地 `skills/<name>/skill.json` + `instructions.md` 必须能被加载并按触发词命中。
- 无效 Skill 目录不得影响 runtime 启动。
- Planner prompt 必须包含命中的 Skill 指令和验收标准。
- ReAct prompt 必须包含命中的 Skill 指令，并只暴露 allowlist 中真实存在的工具。
- runtime 创建任务后必须记录 active Skill，便于日志、session 和后续 replan 复用。

### 147. MCP 和 Skills 缺少 CLI 管理入口，TUI 欢迎页也未提示

#### 现象

- 已有 Skills 运行时能力，但用户新增/查看/删除 Skill 仍需要手动操作目录。
- 项目缺少 MCP server 配置管理入口，无法在面试演示时说明 MCP 接入的配置边界。
- TUI 欢迎页只提示会话和运行命令，没有提示 MCP 与 Skills 管理命令。

#### 修复

- 新增 [src/vora/mcp.py](/Users/liyong/Desktop/ai-manus/src/vora/mcp.py)，提供 MCP server 配置模型、项目级/用户级配置路径、`add/list/remove` 所需读写能力。
- 新增 [src/vora/skills/manager.py](/Users/liyong/Desktop/ai-manus/src/vora/skills/manager.py)，提供 Skill 目录复制、删除和名称校验。
- 在 [src/vora/cli.py](/Users/liyong/Desktop/ai-manus/src/vora/cli.py) 中新增：
  - `vora mcp list/add/remove`
  - `vora skills list/add/remove`
  - `--global` 支持用户级配置或用户级 Skills 目录
  - `--arg -y` 这类以 `-` 开头的 MCP 参数兼容处理
- 在 [src/vora/prompt_tui_formatting.py](/Users/liyong/Desktop/ai-manus/src/vora/prompt_tui_formatting.py) 中把 MCP 和 Skills 管理命令加入欢迎页常用入口。
- 在 [README.md](/Users/liyong/Desktop/ai-manus/README.md) 中补充 MCP 配置命令和 Skills CLI 用法，并明确当前 MCP 只完成配置注册，尚未动态注入 MCP 工具。

#### 回归点

- `vora mcp add/list/remove --cwd <目录>` 必须能写入、展示和删除项目级 MCP server 配置。
- `vora skills add/list/remove --cwd <目录>` 必须能复制、展示和删除项目级 Skill 目录。
- 顶层 help 必须展示 `mcp` 和 `skills` 子命令。
- `mcp --help` 与 `skills --help` 必须展示二级 `list/add/remove`。
- TUI 欢迎页必须提示 `vora mcp list --cwd .` 和 `vora skills list --cwd .`。

### 148. 项目命名和 CLI 入口仍混用旧名称

#### 现象

- 项目已经确定改名为 Vora，但内部包名、CLI 指令、文档示例、测试导入路径和打包配置仍存在旧名称。
- 这会导致面试演示时品牌、命令和代码结构不一致，也会让安装后的入口命令不清晰。

#### 修复

- 将 Python 包从 `src/manus_mini` 迁移为 [src/vora](/Users/liyong/Desktop/ai-manus/src/vora)。
- 将命令入口统一为 `vora`，并同步更新 `pyproject.toml` 的项目名、console script、coverage source 和 mypy 扫描路径。
- 将代码、测试、README、评测脚本和中文文档中的用户可见命令统一改为 `vora`。
- 保留 `.manus-mini` 旧项目本地存储迁移兼容，避免已有 session 和 memory 数据升级后丢失。

#### 回归点

- `python -m vora doctor --cwd <目录>` 必须可用。
- `python -m build` 生成的 sdist/wheel 必须是 `vora-20260702.1644`。
- 全仓搜索不得残留非兼容场景的 `manus_mini`、`manus-mini` 或 `Manus Mini`。
- `.manus-mini` 只允许作为旧数据迁移兼容路径保留。

## 本轮新增/调整测试

- [tests/test_cli.py](/Users/liyong/Desktop/ai-manus/tests/test_cli.py)
  - 增加 `test_cli_doctor_prints_local_setup_without_leaking_api_key`，覆盖本地自检输出、会话数量和 API key 不泄露。
  - 更新 `test_cli_rejects_removed_tui_subcommand`、`test_cli_help_describes_global_options_and_defaults` 和 `test_cli_subcommand_help_describes_cwd_and_force_options`，确认新增 `doctor` 子命令和 help 文案。
  - 复用 `test_cli_without_command_opens_tui` 和 `test_cli_help_does_not_expose_tui_as_command_or_concept`，确认 `vora` 默认进入 TUI 且 `vora tui` 不被重新暴露。
- [tests/test_runtime.py](/Users/liyong/Desktop/ai-manus/tests/test_runtime.py)
  - 增加 `test_runtime_fallback_answers_interview_engineering_questions`，覆盖模型配置、日志产物、上下文压缩、长期记忆、搜索失败和历史会话查看六类面试高频追问。
  - 增加 `test_runtime_fallback_answers_interview_runtime_mechanics_questions`，覆盖 dry-run、命令风险、工具调度、Reflection、eval、架构讲法和打包发布七类面试高频追问。
  - 增加 `test_runtime_fallback_answers_interview_positioning_questions`，覆盖项目边界、Manus 差距、面试演示、工具扩展、生产化、排障和资深度七类面试高频追问。
  - 增加 `test_runtime_fallback_answers_interview_defense_questions`，覆盖核心难点、可观测性、LangChain 取舍、上下文窗口、测试质量、工具失败、幻觉控制和下一版迭代八类面试答辩追问。
  - 增加 `test_runtime_fallback_answers_interview_reliability_questions`，覆盖并发状态、配置环境、损坏 session、数据隔离、命令超时、敏感信息、LLM 空结果和错误分级八类可靠性追问。
  - 增加 `test_runtime_fallback_answers_interview_operational_controls_questions`，覆盖任务取消、日志隐私、工具参数、工具权限、审计追踪、重复读取、长输出落盘和目标漂移八类运行控制追问。
- [tests/test_evals.py](/Users/liyong/Desktop/ai-manus/tests/test_evals.py)
  - 更新 `test_declared_eval_cases_have_unique_runners`，确认 eval case 总数扩展到 12 个。
  - 更新 `test_eval_runner_writes_machine_and_human_reports`，确认 markdown 报告新增分类统计。
- [tests/test_package.py](/Users/liyong/Desktop/ai-manus/tests/test_package.py)
  - 增加 `test_readme_positions_project_as_agent_runtime_not_interview_project`，确认 README 把项目定位为本地 Agent Runtime，而不是面试项目。
- [tests/test_prompt_tui.py](/Users/liyong/Desktop/ai-manus/tests/test_prompt_tui.py)
  - 扩展 `test_format_welcome_explains_limits_and_controls`，覆盖默认 TUI 入口、历史会话查看、写入确认和项目隔离存储位置。
- [tests/test_skills.py](/Users/liyong/Desktop/ai-manus/tests/test_skills.py)
  - 增加 `test_skill_registry_loads_project_skill_and_matches_trigger`，覆盖项目级 Skill 加载、指令读取、触发词匹配和工具 allowlist。
  - 增加 `test_skill_registry_ignores_invalid_skill_directory`，确认坏 Skill 不影响启动。
  - 增加 `test_planner_includes_active_skill_in_prompt`，确认 Planner prompt 注入 active Skill。
  - 增加 `test_react_uses_active_skill_prompt_and_tool_allowlist`，确认 ReAct prompt 注入 active Skill，并按 allowlist 收窄工具候选。
  - 增加 `test_runtime_records_matched_skill_on_task`，确认 runtime 会在 task metadata 中记录命中的 Skill。
- [tests/test_cli.py](/Users/liyong/Desktop/ai-manus/tests/test_cli.py)
  - 增加 `test_cli_mcp_add_list_remove_project_server`，覆盖项目级 MCP server 配置的新增、展示和删除。
  - 增加 `test_cli_skills_add_list_remove_project_skill`，覆盖项目级 Skill 的复制、展示和删除。
  - 更新 help 相关测试，确认 `mcp` / `skills` 子命令以及二级 `list/add/remove` 不被遗漏。
- [tests/test_prompt_tui.py](/Users/liyong/Desktop/ai-manus/tests/test_prompt_tui.py)
  - 扩展 `test_format_welcome_explains_limits_and_controls`，确认欢迎页展示 MCP 和 Skills 管理入口。
- 全仓测试与评测导入路径同步迁移到 `vora` 包名，覆盖 `python -m vora`、`vora` CLI help、MCP/Skills 子命令和 TUI 欢迎页。
## 验证结果

本轮修复完成后，已执行：

```bash
pytest -q
ruff check src tests evals
mypy
python evals/run_evals.py
python -m vora doctor --cwd /private/tmp/vora-doctor-check
pytest --cov=vora --cov-report=term-missing
python -m build
```

结果：

- `pytest -q`：553 passed
- `ruff check src tests evals`：通过
- `mypy`：36 个源码文件无错误
- `python evals/run_evals.py`：12/12 通过
- `python -m vora doctor --cwd /private/tmp/vora-doctor-check`：通过，输出本地存储和 LLM 配置诊断
- `pytest --cov=vora --cov-report=term-missing`：85.26%（门禁 80%）
- `python -m build`：沙箱内因 DNS/PyPI 访问失败，使用外部权限重跑后通过，生成 sdist 和 wheel
