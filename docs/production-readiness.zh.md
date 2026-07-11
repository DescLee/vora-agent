# Vora 生产化方案

## 定位

当前项目是本地单用户 Agent Runtime MVP。它已经具备 Agent 核心链路、工具治理、文件读写直执行策略、上下文压缩、结构化日志和测试体系，但还不是多租户生产平台。

本文说明如果要把它演进为可上线系统，需要补齐哪些工程能力。

## 当前已经具备

| 能力 | 当前实现 |
|---|---|
| 任务状态 | `TaskState` 记录目标、计划、观察、错误、产物和 trace。 |
| 会话状态 | `SessionState` 记录消息、active task、memory refs 和 pending confirmation。 |
| 结构化日志 | `EventLogger` 记录 LLM 请求、工具结果、上下文预算和最终摘要。 |
| 工具超时 | 工具执行支持 timeout 和协作式取消；shell 超时会终止整个进程组。 |
| 工具重试 | retryable tool error 会按上限执行指数退避重试。 |
| 文件读写策略 | `read_file`、`write_file`、`replace_in_file` 按用户要求直接执行；写入保留 diff preview、workspace 边界和 dry-run 不落盘。 |
| 命令确认 | 高风险命令需要 preview + 用户确认。 |
| 质量门禁 | 代码任务 Reflection 阶段执行 pytest gate。 |

## 上线前必须补齐

### 1. 任务队列

当前 runtime 在本地进程内执行。生产环境应将任务提交到队列：

```text
API / CLI
  -> Task Queue
  -> Agent Worker
  -> Tool Sandbox
  -> Artifact Store
  -> Event Log
```

要求：

- 每个任务有 `task_id`、`session_id`、`run_id`。
- worker 可重启，任务状态可恢复。
- 队列支持超时、取消、重试和死信队列。

### 2. 执行隔离

当前命令工具使用本机 subprocess。生产环境应迁移到隔离执行：

- 每个任务使用临时 workspace。
- shell、测试、文件写入在容器或沙箱中执行。
- 默认禁止网络访问，必要时显式授权。
- 限制 CPU、内存、运行时间和输出大小。

### 3. 幂等与恢复

需要把所有副作用分成两阶段：

1. preview：生成 diff、命令计划或写入计划。
2. commit：按策略执行；当前 `write_file` / `replace_in_file` 直接执行，高风险命令仍需确认。

恢复时必须能判断：

- 某个 tool call 是否已经执行。
- 某个写入是否已经应用。
- 是否需要重放、跳过或人工介入。

### 4. 观测性

生产环境至少需要这些指标：

| 指标 | 说明 |
|---|---|
| task_success_rate | 任务最终成功率。 |
| reflection_reject_rate | Reflection 拒绝率，过高说明模型或工具链不稳定。 |
| tool_error_rate | 工具失败率。 |
| confirmation_rate | 命令等高风险动作需要人工确认的比例。 |
| command_reject_rate | 高风险命令拒绝比例。 |
| context_compression_rate | 上下文压缩触发比例。 |
| avg_prompt_tokens / avg_completion_tokens | token 成本。 |
| avg_task_duration | 任务耗时。 |

### 5. 配置治理

当前配置来自环境变量和 `.env`。生产环境需要：

- 按环境区分 dev / staging / production。
- LLM provider、模型名、超时、token 限制可配置。
- 工具权限、路径权限、命令白名单可配置。
- 敏感配置进入 secret manager，不进入日志和 memory。

## 故障处理策略

| 故障 | 当前处理 | 生产化增强 |
|---|---|---|
| LLM 请求失败 | 429/5xx、网络错误和超时会执行指数退避与 jitter，支持 `Retry-After` | 增加多 provider fallback、限流和熔断。 |
| 工具超时 | 设置协作式取消信号；shell 会终止进程组 | 第三方 Python 工具若不响应取消信号，线程仍不能被安全强杀；生产环境需使用进程/容器隔离。 |
| 测试失败 | Reflection 回流继续执行 | 增加失败分类和自动生成最小复现。 |
| 上下文超限 | 压缩或硬裁剪 | 增加 artifact 引用化和分层摘要。 |
| 用户拒绝高风险命令 | 记录取消并继续 | 增加审批记录和审计日志。 |

## 面试表达重点

可以明确说：

> 当前项目是本地单用户 Agent Runtime，用来展示 Agent 工程核心问题的可控实现。生产化不是简单部署，而是要补任务队列、执行隔离、幂等恢复、权限治理和观测指标。

不要说：

> 当前已经是生产级 Agent 平台。
