# Codex 测试与问题修复记录（二）

本文件从第 101 条开始记录。第 1-100 条见 [codex-testing-and-fixes.zh.md](/Users/liyong/Desktop/ai-manus/docs/codex-testing-and-fixes.zh.md)。

## 问题与修复

### 101. 长期记忆追问在 LLM 不可用时缺少工程细节

#### 现象

- 亲自执行 `python -m manus_mini run "长期记忆是怎么工作的？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时这类问题需要讲清存储、检索、路径和敏感信息过滤。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加长期记忆类直接规则兜底。
- 回答说明 SQLite 存储、`project_memory_path`、关键词检索，以及写入前过滤敏感信息。

#### 回归点

- 模型不可用时，长期记忆追问不得展示 `兜底原因`。
- 回答必须包含 `SQLite`、`关键词检索`、`敏感信息`、`project_memory_path`。

### 102. 搜索失败追问在 LLM 不可用时缺少失败策略说明

#### 现象

- 亲自执行 `python -m manus_mini run "如果搜索失败怎么办？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明不会编造结果，以及无搜索结果、网页读取失败、证据不足时的处理方式。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加搜索失败类直接规则兜底。
- 回答明确“未获取到有效搜索结果”、“页面内容读取失败”和“证据不足”，并建议换关键词或补充资料。

#### 回归点

- 模型不可用时，搜索失败追问不得展示 `兜底原因`。
- 回答必须包含失败原因和证据不足说明。

### 103. 查看历史会话追问在 LLM 不可用时缺少 CLI 指引

#### 现象

- 亲自执行 `python -m manus_mini run "怎么查看历史会话？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 用户无法从兜底回答里知道应该先 `list` 再 `resume`。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加历史会话查看类直接规则兜底。
- 回答说明 `manus-mini list --cwd .` 查看 `session_id`、更新时间、消息数和最近用户问题，再用 `manus-mini resume <session_id> --cwd .` 继续。

#### 回归点

- 模型不可用时，历史会话追问不得展示 `兜底原因`。
- 回答必须包含 `manus-mini list`、`manus-mini resume`、`session_id`。

### 104. dry-run 追问在 LLM 不可用时缺少安全预演说明

#### 现象

- 亲自执行 `python -m manus_mini run "dry-run 是怎么工作的？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明 dry-run 如何避免副作用，而不是只知道有一个参数。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加 dry-run 类直接规则兜底。
- 回答说明 `--dry-run` 会返回确认预览和计划动作，但不落盘、不真正执行有副作用操作。

#### 回归点

- 模型不可用时，dry-run 追问不得展示 `兜底原因`。
- 回答必须包含 `--dry-run`、不落盘和确认预览。

### 105. 命令风险追问在 LLM 不可用时缺少工具和确认机制说明

#### 现象

- 亲自执行 `python -m manus_mini run "命令执行怎么控制风险？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要能讲清命令工具、风险判断、拒绝和确认路径。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加命令风险类直接规则兜底。
- 回答说明 `run_bash`、`run_temp_script`、命令风险判断、危险命令拒绝或确认流程。

#### 回归点

- 模型不可用时，命令风险追问不得展示 `兜底原因`。
- 回答必须包含 `run_bash`、`run_temp_script`、命令风险和确认。

### 106. 工具并行调度追问在 LLM 不可用时缺少调度策略说明

#### 现象

- 亲自执行 `python -m manus_mini run "工具并行调度是怎么做的？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要讲清哪些工具能并行、哪些必须串行，以及为什么。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加工具调度类直接规则兜底。
- 回答说明 `ToolScheduler` 会并行无依赖只读工具，写入工具、敏感工具和有依赖工具保持串行。

#### 回归点

- 模型不可用时，工具调度追问不得展示 `兜底原因`。
- 回答必须包含 `ToolScheduler`、只读工具、并行和写入工具。

### 107. Reflection 质量门禁追问被泛化成测试命令清单

#### 现象

- 亲自执行 `python -m manus_mini run "Reflection 质量门禁怎么设计的？" --cwd <目录> --max-steps 1 --max-react 1` 时，返回的是泛化测试命令清单。
- 这不是网络错误兜底，但没有回答 Reflection 的位置、决策和 pytest gate 机制。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加 Reflection 质量门禁类直接规则兜底，并放在泛化测试命令规则之前。
- 回答说明 Reflection 位于 ReAct 草稿之后，代码任务检查测试证据，必要时运行 pytest gate，决策包括 `accept`、`local_update`、`regenerate`、`replan`。

#### 回归点

- Reflection 追问不得退化成单纯测试命令清单。
- 回答必须包含 `Reflection`、`pytest gate`、`accept`、`replan`。

### 108. eval 追问在 LLM 不可用时缺少评测范围说明

#### 现象

- 亲自执行 `python -m manus_mini run "eval 是怎么跑的？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明 eval 命令和覆盖的产品约束，而不是只说跑测试。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加 eval 类直接规则兜底。
- 回答说明 `python evals/run_evals.py` 当前覆盖 9 个关键约束，包括 Reflection、tool exchange、并行调度、写入确认和安全边界。

#### 回归点

- 模型不可用时，eval 追问不得展示 `兜底原因`。
- 回答必须包含 eval 命令、9 个约束、Reflection 和安全边界。

### 109. 架构讲法追问在 LLM 不可用时缺少主链路说明

#### 现象

- 亲自执行 `python -m manus_mini run "这个项目的架构怎么讲？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要用一条清晰主链路讲模块边界。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加架构讲法类直接规则兜底。
- 回答按 `Runtime -> Planner -> ReAct -> ToolScheduler -> Executor/Observer -> Reflection -> Reporter` 说明模块职责。

#### 回归点

- 模型不可用时，架构讲法追问不得展示 `兜底原因`。
- 回答必须包含 `Runtime`、`Planner`、`ReAct`、`ToolScheduler`、`Reflection`。

### 110. 打包发布追问在 LLM 不可用时缺少构建产物说明

#### 现象

- 亲自执行 `python -m manus_mini run "怎么打包发布？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明 Python 包构建入口、产物和提交前门禁。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加打包发布类直接规则兜底。
- 回答说明 `pyproject.toml`、`python -m build`、sdist、wheel、`dist/` 和提交前测试门禁。

#### 回归点

- 模型不可用时，打包发布追问不得展示 `兜底原因`。
- 回答必须包含 `python -m build`、`sdist`、`wheel`、`pyproject.toml`。

### 111. 项目边界与不足追问在 LLM 不可用时缺少诚实边界说明

#### 现象

- 亲自执行 `python -m manus_mini run "这个项目有哪些边界和不足？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时这类问题需要主动讲清本地单用户、非生产级、非容器沙箱、非向量检索等边界。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加项目边界类直接规则兜底。
- 回答说明本地单用户、非生产级、命令执行不是容器沙箱、memory 不是向量检索，以及 LLM adapter 仍缺 streaming 和多 provider。

#### 回归点

- 模型不可用时，项目边界追问不得展示 `兜底原因`。
- 回答必须包含本地单用户、非生产级、向量检索和容器沙箱等关键词。

### 112. 与真正 Manus 的差距追问在 LLM 不可用时缺少定位说明

#### 现象

- 亲自执行 `python -m manus_mini run "和真正的 Manus 比差在哪里？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要避免“复刻完整 Manus”的错觉，明确这是本地 Agent Runtime 工程骨架。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加 Manus 差距类直接规则兜底。
- 回答说明它不是完整 Manus，差距在浏览器自动化、远程沙箱、多租户、任务市场、长周期任务编排和云端可观测平台。

#### 回归点

- 模型不可用时，Manus 对比追问不得展示 `兜底原因`。
- 回答必须包含不是完整 Manus、浏览器自动化、多租户和远程沙箱。

### 113. 面试演示追问在 LLM 不可用时缺少演示路线

#### 现象

- 亲自执行 `python -m manus_mini run "面试时应该怎么演示这个项目？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 用户无法从兜底回答里得到一条可现场展示的路径。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加面试演示类直接规则兜底。
- 回答建议按项目分析、写入确认、Reflection/pytest gate 和会话恢复三段展示。

#### 回归点

- 模型不可用时，面试演示追问不得展示 `兜底原因`。
- 回答必须包含项目分析、写入确认、Reflection 和会话恢复。

### 114. 扩展新工具追问在 LLM 不可用时缺少扩展接口说明

#### 现象

- 亲自执行 `python -m manus_mini run "怎么扩展一个新工具？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要能说明工具协议、注册和测试，而不是只说“加代码”。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加工具扩展类直接规则兜底。
- 回答说明新增工具类、定义 `ToolSpec`、返回 `ToolResult`、注册到 `ToolRegistry`，并补工具、调度和风险测试。

#### 回归点

- 模型不可用时，工具扩展追问不得展示 `兜底原因`。
- 回答必须包含 `ToolSpec`、`ToolRegistry`、`ToolResult` 和测试。

### 115. 生产化缺口追问在 LLM 不可用时缺少取舍说明

#### 现象

- 亲自执行 `python -m manus_mini run "这个项目生产化还缺什么？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要讲清从 MVP 到生产系统还缺哪些工程能力。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加生产化缺口类直接规则兜底。
- 回答说明多租户、容器隔离、权限审计、集中可观测性、配额、密钥托管、任务队列和 SLA。

#### 回归点

- 模型不可用时，生产化追问不得展示 `兜底原因`。
- 回答必须包含多租户、容器隔离、权限审计和可观测性。

### 116. 排障追问在 LLM 不可用时缺少定位链路

#### 现象

- 亲自执行 `python -m manus_mini run "出问题时怎么排障？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明从 session 到日志、trace 和 summary 的排障路径。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加排障类直接规则兜底。
- 回答说明先拿 `session_id`，再查 `logs`、会话 JSON、`trace_events` 和 summary，并沿 LLM 请求、tool call、工具返回、Reflection 决策、最终报告定位。

#### 回归点

- 模型不可用时，排障追问不得展示 `兜底原因`。
- 回答必须包含 `session_id`、`logs`、`trace_events` 和 summary。

### 117. 资深度追问在 LLM 不可用时缺少工程成熟度表达

#### 现象

- 亲自执行 `python -m manus_mini run "为什么说这是一个8年经验水平的项目？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时这类问题需要把项目价值落到边界、安全、可观测、测试等工程成熟度上。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加资深度类直接规则兜底。
- 回答说明它体现的是安全边界、可观测日志、会话恢复、上下文压缩、工具调度、Reflection 质量门禁和测试/eval 门禁。

#### 回归点

- 模型不可用时，资深度追问不得展示 `兜底原因`。
- 回答必须包含边界、安全、可观测和测试。

### 118. TUI 首屏对默认入口和安全行为说明不够直接

#### 现象

- 用户预期 `manus-mini` 直接进入 TUI，`manus-mini tui` 不再作为入口。
- 旧欢迎页只写“当前界面：manus-mini”，缺少 `list`、默认存储位置、写入确认和 dry-run 等实际使用信息。

#### 修复

- 在 [src/manus_mini/prompt_tui_formatting.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/prompt_tui_formatting.py) 中优化 TUI 欢迎页。
- 首屏明确默认 TUI 入口是 `manus-mini --cwd .`，补充 `manus-mini list --cwd .`、`resume`、写入前 diff 确认、dry-run 和 `~/.manus-mini/projects/<project_key>` 存储位置。
- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 的根帮助里补充 `Interactive mode: manus-mini --cwd .`，但不把 `tui` 作为命令或概念重新暴露。
- 保持首屏不展示 `manus-mini tui`，避免用户继续学习已删除入口。

#### 回归点

- `manus-mini` 无子命令必须进入 TUI。
- `manus-mini tui` 必须继续作为非法子命令被拒绝。
- TUI 欢迎页必须展示默认入口、历史会话查看、写入确认和项目隔离存储位置。

### 119. 核心难点追问在 LLM 不可用时缺少工程抓手

#### 现象

- 亲自执行 `python -m manus_mini run "如果面试官问这个项目的核心难点是什么，怎么回答？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试答辩时需要把难点落到工具调用闭环、上下文、安全和验证，而不是泛泛说“做了 Agent”。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加核心难点类直接规则兜底。
- 回答说明难点是工具调用闭环、上下文预算、安全边界和结果验证，并补充可调度、可确认、可回放和测试证据。

#### 回归点

- 模型不可用时，核心难点追问不得展示 `兜底原因`。
- 回答必须包含工具调用闭环、上下文、安全和验证。

### 120. 可观测性追问在 LLM 不可用时缺少日志链路说明

#### 现象

- 亲自执行 `python -m manus_mini run "这个项目怎么体现可观测性？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要能说明如何回看每轮请求、计划、工具调用和 Reflection 决策。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加可观测性类直接规则兜底。
- 回答说明 `EventLogger`、`trace_events`、run `summary` 和项目级 `logs`。

#### 回归点

- 模型不可用时，可观测性追问不得展示 `兜底原因`。
- 回答必须包含 `EventLogger`、`trace_events`、`summary` 和 `logs`。

### 121. 为什么不用 LangChain 追问在 LLM 不可用时缺少取舍表达

#### 现象

- 亲自执行 `python -m manus_mini run "面试官问为什么不用 LangChain，怎么回答？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时这类问题不是框架偏好题，而是要说明项目为了展示底层 Agent 工程可控性。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加 LangChain 取舍类直接规则兜底。
- 回答说明不用框架是为了展示工具调度、确认流、上下文压缩、安全边界和 Reflection 回流等底层机制。

#### 回归点

- 模型不可用时，LangChain 取舍追问不得展示 `兜底原因`。
- 回答必须包含可控性、工具调度、确认流和面试。

### 122. 上下文窗口溢出追问在 LLM 不可用时缺少压缩策略说明

#### 现象

- 亲自执行 `python -m manus_mini run "上下文窗口爆了怎么办？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明分层压缩和 tool exchange 完整性，而不是只说“截断”。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加上下文窗口溢出类直接规则兜底。
- 回答说明 50% / 70% / 90% 分层处理、`CompressionSnapshot` 和 assistant/tool call/result 成组完整。

#### 回归点

- 模型不可用时，上下文窗口追问不得展示 `兜底原因`。
- 回答必须包含 50%、70%、90% 和 `CompressionSnapshot`。

### 123. 测试质量追问在 LLM 不可用时缺少多层门禁说明

#### 现象

- 亲自执行 `python -m manus_mini run "这个项目怎么保证测试质量？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要能讲清单测、lint、类型、eval 和覆盖率的分工。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加测试质量类直接规则兜底。
- 回答说明 `pytest`、`ruff`、`mypy`、`python evals/run_evals.py` 和覆盖率门禁。

#### 回归点

- 模型不可用时，测试质量追问不得展示 `兜底原因`。
- 回答必须包含 `pytest`、`ruff`、`mypy` 和 `eval`。

### 124. 工具调用失败追问在 LLM 不可用时缺少错误模型说明

#### 现象

- 亲自执行 `python -m manus_mini run "如果工具调用失败会怎么处理？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要说明失败如何进入 observation、如何重试，以及如何交给后续回流判断。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加工具失败类直接规则兜底。
- 回答说明失败会转成 `ToolObservation`，保留 `error_code`、summary 和错误说明，可重试错误走重试上限，不可恢复错误交给 ReAct/Reflection。

#### 回归点

- 模型不可用时，工具失败追问不得展示 `兜底原因`。
- 回答必须包含 `ToolObservation`、`error_code`、重试和 Reflection。

### 125. 幻觉控制追问在 LLM 不可用时缺少证据约束说明

#### 现象

- 亲自执行 `python -m manus_mini run "这个项目怎么避免幻觉？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要强调工具结果、文件内容、搜索结果和证据不足时不编造。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加幻觉控制类直接规则兜底。
- 回答说明回答应基于真实 observation、文件内容和搜索结果；证据不足时明确说明不会编造；代码类结果还经过 Reflection 和测试证据检查。

#### 回归点

- 模型不可用时，幻觉控制追问不得展示 `兜底原因`。
- 回答必须包含工具结果、证据、Reflection 和不会编造。

### 126. 下一版迭代追问在 LLM 不可用时缺少优先级判断

#### 现象

- 亲自执行 `python -m manus_mini run "如果让我继续迭代下一版，你会先做什么？" --cwd <目录> --max-steps 1 --max-react 1` 时，只输出网络错误兜底。
- 面试时需要体现产品和工程优先级，而不是随口罗列功能。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加下一版迭代类直接规则兜底。
- 回答优先选择 streaming 输出、容器沙箱、多 provider LLM adapter 和更完整的可观测运行详情视图。

#### 回归点

- 模型不可用时，下一版迭代追问不得展示 `兜底原因`。
- 回答必须包含 streaming、容器沙箱、多 provider 和可观测。

## 本轮新增/调整测试

- [tests/test_runtime.py](/Users/liyong/Desktop/ai-manus/tests/test_runtime.py)
  - 增加 `test_runtime_fallback_answers_interview_engineering_questions`，覆盖模型配置、日志产物、上下文压缩、长期记忆、搜索失败和历史会话查看六类面试高频追问。
  - 增加 `test_runtime_fallback_answers_interview_runtime_mechanics_questions`，覆盖 dry-run、命令风险、工具调度、Reflection、eval、架构讲法和打包发布七类面试高频追问。
  - 增加 `test_runtime_fallback_answers_interview_positioning_questions`，覆盖项目边界、Manus 差距、面试演示、工具扩展、生产化、排障和资深度七类面试高频追问。
  - 增加 `test_runtime_fallback_answers_interview_defense_questions`，覆盖核心难点、可观测性、LangChain 取舍、上下文窗口、测试质量、工具失败、幻觉控制和下一版迭代八类面试答辩追问。
- [tests/test_prompt_tui.py](/Users/liyong/Desktop/ai-manus/tests/test_prompt_tui.py)
  - 扩展 `test_format_welcome_explains_limits_and_controls`，覆盖默认 TUI 入口、历史会话查看、写入确认和项目隔离存储位置。
- [tests/test_cli.py](/Users/liyong/Desktop/ai-manus/tests/test_cli.py)
  - 更新 `test_cli_help_describes_global_options_and_defaults`，确认根帮助展示默认交互入口。
  - 复用 `test_cli_without_command_opens_tui`、`test_cli_rejects_removed_tui_subcommand` 和 `test_cli_help_does_not_expose_tui_as_command_or_concept`，确认 `manus-mini` 默认进入 TUI 且 `manus-mini tui` 不被重新暴露。

## 验证结果

本轮修复完成后，已执行：

```bash
pytest -q
ruff check src tests evals
mypy
python evals/run_evals.py
pytest --cov=manus_mini --cov-report=term-missing
python -m build
```

结果：

- `pytest -q`：528 passed
- `ruff check src tests evals`：通过
- `mypy`：30 个源码文件无错误
- `python evals/run_evals.py`：9/9 通过
- `pytest --cov=manus_mini --cov-report=term-missing`：85.02%（门禁 80%）
- `python -m build`：沙箱内因 DNS/PyPI 访问失败，使用外部权限重跑后通过，生成 sdist 和 wheel
