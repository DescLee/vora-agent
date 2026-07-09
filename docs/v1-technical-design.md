# Manus Mini V1 技术设计文档

## 1. 技术目标

第一版目标是实现一个可运行的 TUI Agent 应用，支持在终端页面中连续对话，并围绕同一个会话迭代完成三类任务：资料调研、本地项目助手、任务自动化。系统需要体现现代 Agent 工程设计，而不是简单的单轮问答。

本文以当前代码实现为准：已经落地的能力直接描述为实现；仍处于演进方向的能力会明确标为后续规划，避免文档承诺超过实际系统。

核心技术关键词：

- Loop Engineering：分为 ReAct 工具循环、Reflection 质量反馈循环、工程兜底重试循环三层。
- Tool Calling：所有外部能力通过工具协议暴露。
- State Machine：任务执行由明确状态驱动。
- Session State：会话历史、当前产物、工具观察和待确认动作统一管理。
- Long-term Memory：跨会话保存用户偏好、项目摘要、产物摘要和重要决策。
- Context Compression：长会话自动压缩旧消息和工具观察，控制上下文规模。
- LLM-assisted Compression：压缩摘要优先由 LLM 生成语义摘要，失败时回退本地规则摘要。
- Human-in-the-loop：文件写入等高风险动作必须人工确认。
- Observability：每一步都有结构化日志和最终执行摘要。
- Testable Agent Core：Loop、工具协议、权限确认可单元测试。

## 2. 推荐技术栈

### 2.1 语言与运行时

推荐使用 Python 3.12+。

理由：

- TUI、文件处理、测试生态成熟。
- Pydantic 适合定义工具 schema 和结构化状态。
- 与 LLM SDK、异步任务、日志系统集成成本低。
- 面试中容易讲清楚工程边界。

### 2.2 关键依赖

- `prompt_toolkit`：构建当前 TUI 页面、消息区、输入区、状态栏、滚动输出和确认面板。
- `rich`：用于 Markdown、表格、日志和确认提示等富文本输出场景。
- `pydantic`：定义任务状态、工具参数、工具结果。
- 标准库 `sqlite3`：保存长期记忆。会话状态使用 JSON 文件持久化。
- `pytest`：单元测试。
- `ruff`：格式与静态检查。
- `urllib.request`：当前 OpenAI-compatible LLM client 的 HTTP 实现。后续可替换为官方 SDK 或多 provider adapter。

## 3. 总体架构

```text
TUI App
 |
 v
SessionManager
 |
 v
AgentRuntime
 |
 +-- Planner
 +-- Executor
 |    |
 |    +-- ToolRegistry
 |    +-- ToolScheduler
 |         +-- Research Tools
 |         +-- Code Tools
 |         +-- Automation Tools
 |
 +-- Observer
 +-- Reflector
 +-- Reporter
 +-- MemoryManager
 +-- ContextCompressor
 +-- EventLogger
```

### 3.1 模块职责

#### TUI App

负责终端页面渲染、用户输入、消息列表、产物预览、状态栏和确认弹窗。

#### SessionManager

负责创建和维护会话状态，包括历史消息、当前任务、当前产物、待确认动作和运行日志位置。第一版可以只支持单会话，第二版再支持恢复历史会话。

#### AgentRuntime

负责每一轮用户消息触发后的 Agent Loop 控制、状态流转、最大步数限制、异常兜底和重试策略。

#### Planner

把用户目标转成初始计划。第一版可以由 LLM 生成，也可以在无 API key 时使用规则化 fallback。

#### Executor

根据当前计划步骤选择工具，校验工具参数，执行工具，并返回结果。

#### ToolScheduler

负责分析同一轮 `tool_calls` 的依赖关系，将可并行工具分批执行，有数据依赖、写入冲突或风险确认的工具保持串行。

#### Observer

把工具返回值转成 Agent 可理解的观察结果，包括成功信息、错误类型、可继续线索。

#### Reflector

评判当前结果是否满足用户目标，决定接受结果、局部更新、重新生成内容或重新规划。当前实现中，非代码任务先直接放过；代码类任务会在 Reflection 阶段生成临时 pytest 验收 case 并执行，不通过时把原始输入、case 和失败原因回流到下一轮执行。

#### Reporter

生成最终 Markdown 结果，包括结论、过程摘要、产物路径、未完成事项。

#### MemoryManager

负责长期记忆的写入、检索和删除。第一版只做轻量本地存储，不引入复杂向量数据库；检索可以先使用关键词、标签和最近更新时间排序。

#### ContextCompressor

负责构建每轮模型输入上下文，并在消息、工具观察或产物版本过长时生成压缩摘要。当前实现先按本地规则分段和保留上下文，保证 tool call / tool result 成组完整；对被移除的旧片段优先请求 LLM 生成语义摘要，失败时回退规则摘要。压缩结果进入会话状态、trace event 和运行日志，避免压缩过程不可追踪。

#### EventLogger

记录结构化事件，支持输出 JSONL 日志和人类可读摘要。

## 4. 目录结构

当前实现目录：

```text
manus-mini/
  pyproject.toml
  README.md
  src/
    manus_mini/
      __init__.py
      prompt_tui.py
      prompt_tui_formatting.py
      runtime.py
      models.py
      llm.py
      session.py
      memory.py
      context.py
      planner.py
      executor.py
      scheduler.py
      observer.py
      reflector.py
      reporter.py
      logging.py
      tools/
        __init__.py
        base.py
        registry.py
        file_tools.py
        research_tools.py
        code_tools.py
        automation_tools.py
        shell_tools.py
        search_tools.py
  tests/
    test_runtime.py
    test_tools.py
    test_prompt_tui.py
    test_memory.py
    test_context.py
  docs/
    v1-product-design.md
    v1-technical-design.md
    adr/
  evals/
    run_evals.py
  .manus-mini/
    sessions/
    logs/
    outputs/
```

## 5. 核心数据模型

### 5.1 SessionState

```python
class SessionState(BaseModel):
    session_id: str
    cwd: Path
    messages: list[Message]
    active_task: TaskState | None = None
    artifacts: list[Artifact] = []
    memory_refs: list[str] = []
    compression_snapshots: list[CompressionSnapshot] = []
    pending_confirmation: PendingConfirmation | None = None
    run_ids: list[str] = []
```

### 5.2 Message

```python
class Message(BaseModel):
    id: str
    role: Literal["user", "agent", "system", "tool"]
    content: str
    created_at: datetime
    tool_call_ids: list[str] = []
    tool_call_id: str | None = None
    metadata: dict[str, Any] = {}
```

说明：

- assistant/agent 消息如果发起工具调用，`tool_call_ids` 记录本轮所有工具调用 id。
- tool 消息必须设置 `tool_call_id`，用于关联发起它的 assistant/agent 工具调用。
- 上下文压缩和硬裁剪必须维护这组关联关系。

### 5.3 TaskState

```python
class TaskState(BaseModel):
    run_id: str
    goal: str
    cwd: Path
    status: Literal["planning", "acting", "observing", "reflecting", "reporting", "done", "failed"]
    plan: list[PlanStep]
    current_step_index: int
    observations: list[Observation]
    artifacts: list[Artifact]
    errors: list[AgentError]
    limits: LoopLimits
    step_count: int
```

### 5.4 LoopLimits

```python
class LoopLimits(BaseModel):
    max_engineering_steps: int = 12
    max_react_iterations: int = 8
    max_reflection_rounds: int = 5
    max_tool_retries: int = 2
    max_estimated_tokens: int = 128_000
```

默认值说明：

- `max_engineering_steps=12`：外层工程兜底循环最多 12 轮，给复杂任务更多拆解空间。
- `max_react_iterations=8`：单个计划步骤最多 8 次 LLM/tool_calls 循环，降低调研类任务过早触顶概率。
- `max_reflection_rounds=5`：质量反馈最多 5 轮，允许更多局部修正但仍避免反复重写。
- `max_tool_retries=2`：单个工具失败最多重试 2 次。
- `max_estimated_tokens=128_000`：用于上下文预算估算，实际可按模型配置覆盖。

### 5.5 PlanStep

```python
class PlanStep(BaseModel):
    id: str
    description: str
    intent: Literal["research", "code", "automation", "report"]
    status: Literal["pending", "running", "done", "skipped", "failed"]
```

### 5.6 ToolSpec

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: type[BaseModel]
    risk_level: Literal["safe", "write", "command"]
```

### 5.7 ToolResult

```python
class ToolResult(BaseModel):
    ok: bool
    summary: str
    data: dict[str, Any] = {}
    error: AgentError | None = None
    artifacts: list[Artifact] = []
```

### 5.8 MemoryItem

```python
class MemoryItem(BaseModel):
    id: str
    scope: Literal["user", "project", "session", "artifact"]
    kind: Literal["preference", "project_summary", "artifact_summary", "decision", "constraint"]
    content: str
    tags: list[str] = []
    confidence: float = 1.0
    source_message_ids: list[str] = []
    created_at: datetime
    updated_at: datetime
```

### 5.9 CompressionSnapshot

```python
class CompressionSnapshot(BaseModel):
    id: str
    covered_message_ids: list[str]
    covered_observation_ids: list[str]
    summary: str
    retained_facts: list[str]
    open_questions: list[str] = []
    artifact_refs: list[str] = []
    created_at: datetime
```

### 5.10 ContextBundle

```python
class ContextBundle(BaseModel):
    current_user_message: Message
    recent_messages: list[Message]
    relevant_memories: list[MemoryItem]
    compression_summaries: list[CompressionSnapshot]
    active_artifacts: list[Artifact]
    recent_observations: list[Observation]
```

## 6. Agent Loop 设计

TUI 里的 Agent Loop 不是进程启动后只跑一次，而是每次用户输入消息都会触发一轮任务处理。首轮消息通常创建新任务，后续消息通常基于当前产物和会话历史创建修改任务。

V1 的 Loop Engineering 明确分为三层：

1. ReAct 工具循环：LLM 思考是否需要工具，返回 `tool_calls`；Agent 调用工具并把观察结果交回 LLM；循环直到 LLM 不再需要工具并产出草稿结果。
2. Reflection 质量反馈循环：专门的 Reflector 评判草稿结果是否满足目标，并决定接受、局部更新、重新生成内容或重新生成计划。
3. 工程兜底循环：Runtime 统一处理最大迭代次数、token 成本、工具异常、模型异常和重试降级。

### 6.1 三层循环总览

```text
User Message
  |
  v
Engineering Guard Loop
  |
  +-- Reflection Loop
       |
       +-- ReAct Tool Loop
            |
            +-- LLM think -> tool_calls? -> Agent execute tools -> observe -> LLM think
       |
       +-- Reflect result -> accept / local_update / regenerate / replan
  |
  +-- handle timeout / max iterations / token budget / tool errors / fallback
  |
  v
Render Agent Message + Artifact Preview
```

### 6.2 外层：工程兜底循环

工程兜底循环由 `AgentRuntime` 控制，目标是防止 Agent 无限运行或因为局部错误直接崩溃。

伪代码：

```python
def on_user_message(content: str, session: SessionState) -> SessionState:
    session.messages.append(Message.user(content))
    context = context_builder.build(session)
    task = runtime.create_or_update_task(content, session, context)
    task.plan = planner.create_plan(task, session, context)

    while runtime.within_engineering_limits(task):
        task.step_count += 1

        try:
            draft = reflection_loop.run(task, session, context)
            if draft.accepted:
                task.result = draft.result
                break

            task = runtime.apply_reflection_decision(task, draft.decision)
        except RetryableAgentError as error:
            task = runtime.retry_or_degrade(task, error)
        except NonRetryableAgentError as error:
            task = runtime.fail_with_partial_result(task, error)
            break

        context = context_builder.rebuild_if_needed(session, task)

    memory_manager.extract_and_store(session, task)
    session = context_compressor.compress_if_needed(session, task)
    agent_message = reporter.build_message(task, session)
    session.messages.append(agent_message)
    session.active_task = task
    return session
```

第一版三层循环都必须设置最大循环次数：

- 外层工程兜底循环：`task.limits.max_engineering_steps`。
- 中层 Reflection 循环：`task.limits.max_reflection_rounds`。
- 内层 ReAct 工具循环：`task.limits.max_react_iterations`。

同时还需要限制工具重试次数和上下文预算：

- `task.limits.max_tool_retries`：单个工具调用最大重试次数。
- `task.limits.max_estimated_tokens`：上下文预算上限。

工程兜底策略：

- 达到 `max_engineering_steps`：输出部分结果、未完成原因和建议下一步。
- 达到 `max_react_iterations`：停止继续调用工具，交给 Reflection 判断部分结果是否可用。
- 达到 `max_reflection_rounds`：保留当前最佳结果，并在回复中标记质量风险。
- token 成本过高：触发上下文压缩；压缩后仍超限则裁剪低优先级上下文。
- 工具调用超时：按工具重试策略重试，仍失败则降级或跳过。
- LLM 返回非法结构：重试结构化输出；仍失败则 fallback 到规则化计划。
- 用户拒绝写入：作为正常观察，不视为系统错误。

注意：`step_count` 必须在每轮开始时递增。用户拒绝写入、工具参数错误等非成功路径也要消耗一次循环额度，避免 Agent 在同一动作上无限重试。

### 6.3 中层：Reflection 质量反馈循环

Reflection Loop 负责判断 ReAct Loop 产出的结果是否满足用户目标。它不是简单地问“完成了吗”，而是要给出明确跳转决策。

Reflector 输出：

```python
class ReflectionDecision(BaseModel):
    action: Literal["accept", "local_update", "regenerate", "replan"]
    reason: str
    target_step_id: str | None = None
    feedback: str
```

决策含义：

- `accept`：结果符合要求，进入 Reporter。
- `local_update`：局部修改，例如只改报告第二节、只补一个缺失字段。
- `regenerate`：当前产物整体质量不达标，但计划仍然正确，需要重新生成结果。
- `replan`：计划方向错误、资料不足或用户意图变化，需要重新规划步骤。

伪代码：

```python
def run_reflection_loop(task: TaskState, session: SessionState, context: ContextBundle) -> DraftResult:
    rounds = 0

    while rounds < task.limits.max_reflection_rounds:
        draft = react_loop.run(task, session, context)
        decision = reflector.evaluate(draft, task, session, context)

        if decision.action == "accept":
            return DraftResult(accepted=True, result=draft, decision=decision)

        if decision.action == "local_update":
            task = planner.create_local_update_task(task, decision.feedback)
        elif decision.action == "regenerate":
            task = planner.create_regeneration_task(task, decision.feedback)
        elif decision.action == "replan":
            task.plan = planner.replan(task, decision.feedback, context)

        rounds += 1

    return DraftResult(
        accepted=True,
        result=draft,
        decision=ReflectionDecision(
            action="accept",
            reason="达到最大反思轮次，保留当前最佳结果",
            feedback="",
        ),
    )
```

V1 评判维度：

- 是否回答了用户当前消息。
- 是否遵守长期记忆中的用户偏好。
- 是否基于当前产物进行修改，而不是丢失上下文重新生成。
- 是否有明显缺失、重复、格式错误或事实冲突。
- 是否触发了需要确认的文件修改。

### 6.4 内层：ReAct 工具循环

ReAct Loop 发生在 LLM 与工具调用之间。LLM 每轮根据当前上下文决定是否需要工具：

- 如果需要工具，返回 `tool_calls`。
- Agent 先分析 `tool_calls` 是否可并行，再执行指定工具并返回观察结果。
- LLM 基于观察结果继续思考。
- 当不再需要工具时，LLM 返回当前步骤的草稿结果。

伪代码：

```python
def run_react_loop(task: TaskState, session: SessionState, context: ContextBundle) -> StepDraft:
    react_iterations = 0
    messages = prompt_builder.build_react_messages(task, session, context)

    while react_iterations < task.limits.max_react_iterations:
        llm_result = llm.complete_with_tools(messages, tool_registry.specs())

        if not llm_result.tool_calls:
            return StepDraft(content=llm_result.content, observations=task.observations)

        scoped_tool_calls, rejected_results = tool_policy.apply_iteration_budget_and_scope(
            llm_result.tool_calls,
            task,
            session,
        )
        task.observations.extend(observer.from_tool_result(result) for result in rejected_results)

        batches = tool_scheduler.plan_batches(scoped_tool_calls, context)

        for batch in batches:
            results = executor.execute_batch_with_policy(batch, session, context)
            observations = [observer.from_tool_result(result) for result in results]
            task.observations.extend(observations)
            messages.extend(Message.tool(item.to_prompt_text()) for item in observations)

        react_iterations += 1

    messages.append(
        Message.system("已达到工具循环上限，请基于现有上下文直接输出最终答案，不要再请求任何工具。")
    )
    final_draft = llm.complete_with_tools(messages, [])
    return StepDraft(content=final_draft.content or "已达到工具循环上限，保留当前最佳结果。", observations=task.observations)
```

ReAct Loop 的边界：

- 只负责完成当前计划步骤，不负责判断最终质量。
- 不直接写文件，写入动作仍要走工具风险策略和用户确认。
- 不处理全局重试策略，工具异常向外抛给工程兜底循环。
- 达到 `max_react_iterations` 后不再报错，而是强制进行一次无工具的最终收口，让模型基于已有上下文输出结果。
- 每轮执行工具前必须先应用预算和范围策略：默认最多执行 5 个 tool calls，其中 `read_file` 最多 3 个、`list_files` 最多 1 个；超出部分转成 `TOOL_CALL_BUDGET_EXCEEDED` observation。
- 对“项目概览/优化建议/想法总结”类任务，`read_file` 第一阶段只允许读取 README、项目元数据、docs 文档和核心入口文件；越界读取转成 `PROJECT_SCOPE_RESTRICTED` observation，不直接读取任意源码全文。

### 6.5 工具并行调度

LLM 一轮可能返回多个 `tool_calls`。Agent 不应默认串行执行，而应先做依赖分析：

```text
tool_calls -> dependency analysis -> execution batches -> parallel within batch -> observe
```

调度原则：

- 无共享写入、无数据依赖、只读或安全工具可以并行。
- 后一个工具需要前一个工具输出时必须串行。
- 写同一个文件、移动同一路径、创建同一目录等有资源冲突的工具必须串行。
- 需要用户确认的写入工具必须单独成批，确认后再执行。
- `command` 风险工具第一版默认不开放；后续开放时也默认串行。

V1 依赖判断可以先使用保守规则：

```python
class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any]
    depends_on: list[str] = []
    resource_keys: list[str] = []
    risk_level: Literal["safe", "write", "command"]
```

`resource_keys` 用于描述工具会访问的资源，例如：

```text
file:docs/a.md
dir:src
artifact:research-report.md
```

分批算法：

```python
def plan_batches(tool_calls: list[ToolCall]) -> list[list[ToolCall]]:
    graph = build_dependency_graph(tool_calls)
    batches = []

    while graph.has_ready_nodes():
        ready = graph.ready_nodes()
        batch = []

        for call in ready:
            if conflicts_with_any(call, batch):
                continue
            if call.risk_level != "safe":
                continue
            batch.append(call)

        if batch:
            batches.append(batch)
            graph.mark_scheduled(batch)
            continue

        # 写入、确认、命令等高风险动作保守串行。
        call = graph.next_ready_node()
        batches.append([call])
        graph.mark_scheduled([call])

    return batches
```

执行方式：

```python
async def execute_batch_with_policy(batch: list[ToolCall], session: SessionState, context: ContextBundle) -> list[ToolResult]:
    if len(batch) == 1:
        return [await executor.execute_one_with_policy(batch[0], session, context)]

    return await asyncio.gather(
        *(executor.execute_one_with_policy(call, session, context) for call in batch),
        return_exceptions=False,
    )
```

用户体验要求：

- TUI 状态栏展示当前批次，例如 `tools: 3 parallel reads`。
- 对话区记录并行批次摘要，而不是刷屏展示每个底层事件。
- 日志中保留每个工具的开始时间、结束时间、批次编号和依赖关系，方便面试展示性能优化。

### 6.6 多轮产物迭代

后续用户输入需要带上当前会话上下文：

- 最近几轮用户要求。
- 当前产物路径和内容摘要。
- 最近工具观察。
- 用户拒绝或确认过的高风险动作。

例如用户说“第二部分太泛了”，Planner 不应当当作新任务处理，而应识别为对当前报告的局部修改任务。

### 6.7 长期记忆策略

第一版的长期记忆不追求语义检索能力极强，而是追求边界清楚、可解释、可测试。

存储位置：

```text
.manus/
  memory.db
  sessions/
    <session_id>.json
```

写入条件：

- 用户明确表达的偏好，例如报告风格、语言、输出结构。
- 项目稳定信息，例如技术栈、目录结构、关键文件职责。
- 产物摘要，例如当前报告主题、章节结构、最后修改点。
- 明确决策，例如“第一版不做 Web，只做 TUI”。

不写入条件：

- 未确认的猜测。
- 临时错误和工具失败堆栈。
- 大段原文内容。
- 涉及密钥、token、隐私数据的内容。

检索策略：

- 根据当前用户消息提取关键词。
- 按 `scope` 优先级检索：当前项目 > 用户偏好 > 历史产物 > 普通会话。
- 同类记忆按 `updated_at` 和关键词命中数排序。
- 每轮最多注入 5 条记忆，避免长期记忆污染上下文。

删除策略：

- 用户输入“忘记/不要记住/删除偏好”时，生成删除候选并请求确认。
- 删除操作记录到事件日志。

### 6.8 上下文压缩策略

第一版采用三层上下文：

1. Recent Context：最近 N 条消息和观察，原文保留。
2. Compressed Context：较早消息压缩成摘要，保留目标、约束、决策、产物变化。
3. Long-term Memory：跨会话检索出的稳定信息。

触发条件：

- 消息数超过阈值，例如 20 条。
- 工具观察总字符数超过阈值，例如 12,000 字符。
- 产物版本超过阈值，例如同一报告被修改 3 次以上。
- 预估 token 达到模型上下文预算的 70%。

压缩输出必须包含：

- 用户原始目标。
- 已确认的需求和约束。
- 已完成动作。
- 当前产物路径和结构。
- 未完成事项。
- 被用户拒绝的动作。

压缩后保留策略：

- 最近 6 条消息保留原文。
- 最近 3 条工具观察保留原文。
- 当前产物只注入摘要和必要片段，不全量注入。
- 压缩摘要写入 `SessionState.compression_snapshots`，同时进入日志。

上下文消息完整性约束：

- 不能按单条消息粗暴裁剪工具调用相关消息。
- 带 `tool_calls` 的 assistant 消息和对应的 tool result 消息必须成组保留或成组删除。
- 任何保留下来的 tool result 都必须能找到对应的 `tool_call_id`。
- 任何保留下来的 assistant `tool_calls` 都必须包含所有对应 tool result，除非这组消息整体被压缩成摘要并从模型输入中移除。
- 压缩摘要可以描述工具调用结论，但不能伪造原始 `tool_call_id` 继续传给模型。

实现上先把消息切成 `ContextSegment`，再做预算裁剪：

```python
class ContextSegment(BaseModel):
    id: str
    kind: Literal["plain_message", "tool_exchange", "summary", "memory", "artifact"]
    messages: list[Message] = []
    estimated_tokens: int
    priority: int
```

其中 `tool_exchange` 是不可拆分单元：

```text
assistant message with tool_calls
+ tool result message for tool_call_id A
+ tool result message for tool_call_id B
+ ...
```

硬裁剪只能删除整个 `ContextSegment`。裁剪完成后必须运行完整性校验：

```python
def validate_tool_call_pairs(messages: list[Message]) -> None:
    requested_ids = set()
    answered_ids = set()

    for message in messages:
        if message.role == "assistant":
            requested_ids.update(message.tool_call_ids)
        if message.role == "tool":
            answered_ids.add(message.tool_call_id)

    orphan_results = answered_ids - requested_ids
    missing_results = requested_ids - answered_ids

    if orphan_results or missing_results:
        raise ContextIntegrityError(
            orphan_results=orphan_results,
            missing_results=missing_results,
        )
```

如果校验失败，必须回退到上一个合法 `ContextBundle`，或把整组工具交换压缩成普通摘要后再重建上下文。

### 6.9 上下文预算估算

第一版不接入精确 tokenizer，使用可解释的近似估算即可。目标是判断是否需要压缩，而不是精确计费。

核心公式：

```text
context_usage = estimated_tokens(context_bundle) / model_context_limit
```

`ContextBundle` 由以下部分组成：

```text
当前用户消息
+ 最近 N 条消息原文
+ 最近 M 条工具观察
+ 当前产物摘要
+ 当前产物必要片段
+ 压缩摘要
+ 检索到的长期记忆
+ 系统提示词和工具说明预算
```

估算规则：

```text
中文文本: token ≈ 中文字符数 * 1.2
英文文本: token ≈ 英文单词数 * 1.3
代码内容: token ≈ 字符数 / 3
普通混合文本: token ≈ 字符数 / 2
```

实现上可以先按内容类型传入 `estimate_tokens(text, kind)`：

```python
def estimate_tokens(text: str, kind: Literal["zh", "en", "code", "mixed"]) -> int:
    if kind == "zh":
        return int(len(text) * 1.2)
    if kind == "en":
        return int(len(text.split()) * 1.3)
    if kind == "code":
        return max(1, len(text) // 3)
    return max(1, len(text) // 2)
```

V1 默认预算分配：

```text
system/tool schema: 20%
recent messages:    25%
tool observations:  20%
artifact snippets:  20%
long-term memory:   10%
reserved output:     5%
```

触发规则：

```text
context_usage > 0.50: 策略一，压缩过长工具消息，保留首尾并提示中间压缩字符数
context_usage > 0.70: 策略二，压缩中间历史消息，system/头部用户消息/最近消息保持原文
context_usage > 0.90: 策略三，强制截断低相关历史，必要时改写过长的最近用户消息
```

压缩触发点固定为两处：

```text
after_user_message: 用户消息写入会话后，同步压缩完成再进入 Planner/ReAct
after_llm_message: LLM assistant 消息写入会话后，同步压缩完成再执行工具
```

策略升级规则：如果策略一执行后上下文仍超过 50%，继续执行策略二；策略二后仍超过 70%，继续执行策略三。策略三也会在初始上下文超过 90% 时直接参与。策略二优先请求当前 LLM 生成中文语义摘要，失败、空输出或没有 LLM 时回退规则摘要。压缩快照通过 `CompressionSnapshot.metadata` 记录 `strategy`、`trigger_stage`、`summary_source`、`compressed_chars` 等字段。

硬裁剪时的保留优先级：

1. 当前用户消息。
2. 当前任务目标、约束和成功标准。
3. 最近几轮对话。
4. 当前产物摘要与相关片段。
5. 高置信长期记忆。
6. 压缩摘要。
7. 低相关工具观察。

注意：上述优先级作用在 `ContextSegment` 上，而不是单条消息上。工具调用交换组必须保持完整，避免产生孤儿 `tool_call_id` 导致模型接口报错。

压缩日志写入 `events.jsonl` 对应的会话节点日志：

```json
{
  "type": "context_compression_completed",
  "trigger_stage": "after_llm_message",
  "strategies": ["tool_message", "history_summary"],
  "before_tokens": 64000,
  "after_tokens": 31000,
  "before_usage": 0.50,
  "after_usage": 0.24,
  "summary_source": "llm",
  "covered_message_count": 18,
  "compressed_chars": 12000,
  "snapshot_id": "compression_xxx"
}
```

日志中需要记录每轮估算结果：

```json
{
  "type": "context_budget",
  "estimated_tokens": 42000,
  "model_context_limit": 128000,
  "context_usage": 0.33,
  "compression_triggered": false
}
```

## 7. 工具协议

所有工具必须满足统一接口：

```python
class Tool(Protocol):
    spec: ToolSpec

    def preview(self, params: BaseModel, context: ToolContext) -> ToolPreview:
        ...

    def run(self, params: BaseModel, context: ToolContext) -> ToolResult:
        ...
```

工具分为三类风险：

- `safe`：只读工具，如列目录、读文件、摘要文本。
- `write`：写文件、创建目录、移动文件，必须确认。
- `command`：执行 shell 命令，第一版默认不开放，第二版再做白名单。

工具需要提供调度元数据：

```python
class ToolResourceSpec(BaseModel):
    read_keys: list[str] = []
    write_keys: list[str] = []
    can_parallel: bool = True
```

规则：

- 只读工具默认 `can_parallel=True`。
- 写入工具默认 `can_parallel=False`，除非写入资源完全不同且无需用户逐个确认。
- 工具实现应尽量准确声明 `read_keys` 和 `write_keys`，例如 `file:README.md`、`dir:docs`。
- 调度器可以覆盖工具声明，采取更保守的串行策略。

## 8. 第一版工具清单

### 8.1 File Tools

- `list_files(path, max_depth)`
- `read_file(path)`
- `write_file(path, content)`
- `append_file(path, content)`
- `make_directory(path)`

### 8.2 Research Tools

- `collect_local_docs(paths)`
- `summarize_text(content)`
- `generate_markdown_report(title, sections)`

### 8.3 Code Tools

- `scan_project(path)`
- `read_code_file(path)`
- `propose_patch(path, instruction)`
- `apply_text_edit(path, old_text, new_text)`

### 8.4 Automation Tools

- `extract_todos(content)`
- `organize_notes(paths)`
- `generate_checklist(items)`

## 9. 权限与安全

第一版规则：

- 默认只能访问 `cwd` 内文件。
- 所有路径必须 normalize 后校验，禁止 `../` 逃逸工作区。
- 写入工具必须展示 preview 并等待确认。
- 默认不执行 shell 命令。
- `dry-run` 模式下不允许真实写入。
- 每次写入前保存原内容摘要，方便第二版扩展回滚。

文件写入确认流程：

```text
prepare -> preview diff/summary -> user confirm -> write -> observe
```

## 10. 日志与产物

每次运行创建独立目录：

```text
runs/<run_id>/
  events.jsonl
  summary.md
outputs/
  <artifact>.md
```

事件结构：

```json
{
  "run_id": "20260630-091500",
  "step": 3,
  "type": "tool_call",
  "tool": "read_file",
  "summary": "读取 README.md",
  "ok": true,
  "duration_ms": 12
}
```

并行工具批次事件：

```json
{
  "run_id": "20260630-091500",
  "step": 3,
  "type": "tool_batch",
  "batch_id": "batch-002",
  "parallel": true,
  "tools": ["read_file:docs/a.md", "read_file:docs/b.md", "read_file:docs/c.md"],
  "duration_ms": 48
}
```

## 11. LLM 抽象

第一版不要把业务逻辑绑死在某个模型 SDK 上。

```python
class LLMClient(Protocol):
    def complete_structured(self, prompt: str, schema: type[BaseModel]) -> BaseModel:
        ...
```

建议提供两个实现：

- 测试 LLM stub：用于测试和无网络演示。
- `OpenAICompatibleClient`：用于真实模型调用。

Planner、Reflector、Reporter 都依赖 `LLMClient` 接口，不直接依赖具体 SDK。

## 12. 异常处理

第一版错误类型：

```python
class AgentError(BaseModel):
    code: Literal[
        "FILE_NOT_FOUND",
        "PATH_OUT_OF_WORKSPACE",
        "INVALID_TOOL_PARAMS",
        "USER_CANCELLED",
        "MAX_STEPS_REACHED",
        "MAX_REACT_ITERATIONS_REACHED",
        "MAX_REFLECTION_ROUNDS_REACHED",
        "TOKEN_BUDGET_EXCEEDED",
        "TOOL_TIMEOUT",
        "TOOL_RETRY_EXHAUSTED",
        "INVALID_LLM_OUTPUT",
        "LLM_ERROR",
        "UNKNOWN_ERROR",
    ]
    message: str
    retryable: bool = False
```

处理策略：

- `FILE_NOT_FOUND`：建议列目录或询问用户。
- `PATH_OUT_OF_WORKSPACE`：直接拒绝。
- `INVALID_TOOL_PARAMS`：交给 Reflector 重新规划。
- `USER_CANCELLED`：记录观察，继续或生成替代方案。
- `MAX_STEPS_REACHED`：外层工程兜底循环达到上限，输出部分结果和未完成事项。
- `MAX_REACT_ITERATIONS_REACHED`：交给 Reflection 判断是否接受部分结果或重新规划。
- `MAX_REFLECTION_ROUNDS_REACHED`：保留当前最佳结果，并说明质量风险。
- `TOKEN_BUDGET_EXCEEDED`：先压缩上下文，仍超限则裁剪低优先级上下文。
- `TOOL_TIMEOUT`：按工具重试策略重试，仍失败则降级或跳过。
- `TOOL_RETRY_EXHAUSTED`：记录工具失败，交给 Reflection 决定替代路径。
- `INVALID_LLM_OUTPUT`：重试结构化输出，仍失败则使用规则 fallback。
- `LLM_ERROR`：使用 rule fallback 或失败退出。

## 13. 测试策略

第一版至少覆盖：

- Runtime 在最大步数内正常结束。
- Runtime 达到最大步数时输出部分结果。
- ReAct Loop 能在 LLM 返回 `tool_calls` 时调用工具，并把观察结果传回下一轮。
- ReAct Loop 达到最大工具循环次数时能抛出可重试错误。
- ToolScheduler 能把无依赖只读工具分到同一个并行批次。
- ToolScheduler 能把有 `depends_on`、共享写入资源或需要确认的工具拆成串行批次。
- 并行批次中单个工具失败时能按工程兜底策略处理，不丢失其他工具观察。
- Reflection Loop 能根据评判结果进入 `accept`、`local_update`、`regenerate`、`replan` 分支。
- Reflection Loop 达到最大反思次数时能保留当前最佳结果并标记质量风险。
- 工程兜底循环能处理工具超时、非法 LLM 输出、token 超限和重试耗尽。
- 工程兜底循环达到最大步数时不会继续调用 LLM 或工具。
- 同一 Session 中后续消息能基于当前产物继续修改。
- TUI 输入消息后能追加用户消息、触发任务并渲染 Agent 回复。
- 长期记忆能保存用户偏好，并在下一轮相关请求中被检索出来。
- 上下文过长时能生成 CompressionSnapshot，并保留最近消息原文。
- 上下文硬裁剪不能产生孤儿 `tool_call_id`：assistant tool_calls 和对应 tool result 必须成组保留或成组删除。
- ContextCompressor 在裁剪后必须运行工具调用配对校验，失败时回退或重建合法上下文。
- 敏感内容和未确认猜测不会写入长期记忆。
- 只读工具不能写文件。
- 写入工具在未确认时不会修改文件。
- 路径逃逸被拒绝。
- ToolResult 错误能被 Observer 转成 Observation。
- Reporter 能生成 Markdown 摘要。

推荐命令：

```bash
pytest
ruff check .
```

## 14. 第二版技术演进

第二版重点：

- 引入完整权限系统：按工具、路径、风险等级授权。
- 引入命令工具：只允许白名单命令，默认超时。
- 引入任务持久化：支持 resume。
- 引入 eval：固定任务集、期望产物、自动评分。
- 引入多模型策略：规划模型、执行模型、总结模型分离。
- 引入 trace 可视化：从 JSONL 生成运行时间线。
- 引入 patch 模式：使用统一 diff，而不是简单字符串替换。
- 升级上下文压缩：多级摘要、按任务阶段保留上下文、压缩质量评测。
- 升级长期记忆：向量检索、记忆置信度、冲突检测、用户可视化管理。
- 引入人工问题节点：歧义过大时主动停下询问。

## 15. 实施顺序建议

第一版建议按以下顺序实现：

1. 建立项目骨架和 TUI 应用入口。
2. 定义 `models.py` 中的核心数据模型。
3. 实现 `SessionManager`，跑通连续消息输入和状态保存。
4. 实现 `MemoryManager` 和本地长期记忆存储。
5. 实现 `ContextCompressor` 和 `ContextBundle` 构建。
6. 实现 Tool 基类、Registry 和文件工具。
7. 实现 Runtime 主循环。
8. 实现 Mock Planner/Reflector/Reporter，先跑通闭环。
9. 接入真实 LLMClient。
10. 增加 Research/Code/Automation 工具。
11. 增加日志、产物目录和基础测试。

验收标准：

- 三类任务各有一个 demo 可以跑通。
- 至少一个 demo 能连续对话两轮以上，并修改上一轮产物。
- 至少一个 demo 能展示三层最大循环次数：`step x/8`、`react x/5`、`reflect x/3`。
- 至少一个 demo 能记住用户偏好，并在后续回复中使用。
- 至少一个长会话 demo 能触发上下文压缩，并展示压缩摘要。
- 至少一个 demo 会触发文件写入确认。
- `runs/<run_id>/events.jsonl` 可解释每一步发生了什么。
- 测试能证明权限确认、路径限制、记忆写入过滤和压缩触发有效。
