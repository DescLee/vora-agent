# manus-mini

本地 Agent Runtime

`manus-mini` 是一个本地 Agent Runtime。项目重点不是复刻完整 Manus 产品，而是实现一个可运行、可测试、可观察、带安全边界的 Agent 工程骨架；它可以作为候选人在面试中讲解 Agent 工程能力的项目。

## 项目定位

当前版本重点体现：

- Agent 运行编排：`Runtime -> Planner -> ReAct -> ToolScheduler -> Executor -> Observer -> Reflection -> Reporter`
- 工具调用治理：工具 schema、批次调度、依赖处理、写入确认、命令风险判断。
- 状态化会话：会话历史、任务状态、工具观察、产物、待确认动作统一建模。
- 上下文工程：工具调用成组完整性校验、50/70/90 分层压缩、LLM 语义压缩 + 规则回退、token 预算与压缩日志。
- 质量门禁：代码类任务在 Reflection 阶段生成并执行真实 pytest 验收 case。
- 可观测性：结构化事件日志、运行摘要、Markdown 产物。
- 可测试性：核心 Agent 流程、工具协议、安全边界、交互展示均有自动化测试。

## 当前已实现

| 能力 | 状态 | 说明 |
|---|---:|---|
| 交互恢复界面 | 已实现 | 通过 `resume` 进入历史会话，支持连续输入、状态展示、确认面板。 |
| OpenAI-compatible LLM | 已实现 | 通过 `/chat/completions` 接入，支持 function/tool calls。 |
| ReAct 工具循环 | 已实现 | 模型请求工具、执行工具、回填 observation，直到生成结果。 |
| Reflection 质量门禁 | 已实现 | 非代码任务先直接放过；代码任务会运行临时 pytest gate。 |
| 工具调度 | 已实现 | 只读工具可批量并行，敏感/写入/命令工具串行。 |
| 文件工具 | 已实现 | list/read/write/replace/append/mkdir，含路径限制和写入确认。 |
| 命令工具 | 已实现 | bash/temp script，含禁用模式、风险判断、确认和超时。 |
| 长期记忆 | 已实现 | SQLite 存储，敏感信息过滤，关键词检索。 |
| 会话持久化 | 已实现 | list/resume/delete/clear，支持中断后修复 tool message。 |
| 上下文压缩 | 已实现 | 用户消息后和 LLM 返回后同步压缩；按 50/70/90 阈值压缩工具输出、摘要历史、强制截断；压缩时保持 assistant/tool exchange 成组完整。 |
| 启动自检 | 已实现 | `manus-mini doctor` 可检查本地存储路径、会话数量和 LLM 配置完整性。 |
| eval 雏形 | 已实现 | `evals/run_evals.py` 可验证关键 Agent 行为。 |

## 当前边界

这些能力有设计说明或演进路径，但当前还不是生产级实现：

- 非代码任务 Reflection 目前只直接放过，下一版再接结构化验收 case。
- 命令执行仍是本机 subprocess，不是容器或强沙箱。
- memory 是 SQLite + 关键词检索，不是向量检索或分层记忆系统。
- LLM adapter 是 OpenAI-compatible 实现，已支持瞬时 HTTP/网络错误基础重试；尚未接 streaming、多 provider、指数退避。
- 当前是本地单用户 runtime，不是多租户服务端 Agent 平台。

## 安装

```bash
pip install -e ".[dev]"
```

## 配置

配置读取顺序：环境变量、当前运行目录 `.env`、用户级 `~/.manus-mini/.env`、源码根目录 `.env`。

```env
LLM_PROVIDER=openai-compatible
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=your-api-key
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT_SECONDS=120
LLM_MAX_ATTEMPTS=3
LLM_RETRY_BACKOFF_SECONDS=0.25
```

## 运行

```bash
manus-mini
manus-mini doctor --cwd .
manus-mini run "总结一下当前项目" --cwd .
```

常用参数：

```bash
manus-mini --cwd . --max-steps 3 --max-react 99 --max-reflect 3 --dry-run
manus-mini doctor --cwd .
manus-mini run "总结一下当前项目" --cwd . --max-steps 3 --max-react 99 --max-reflect 3 --dry-run
manus-mini list
manus-mini resume <session_id> --cwd . --max-steps 3 --max-react 99 --max-reflect 3 --dry-run
```

## 验证

```bash
pytest -q
ruff check src tests evals
mypy
pytest --cov=manus_mini --cov-report=term-missing
python evals/run_evals.py
```

## 项目讲解建议

推荐展示三条路径：

1. 项目分析：让 Agent 扫描当前项目并生成结构化总结。
2. 写入确认：让 Agent 修改一个文件，展示 diff preview 和人工确认。
3. 代码门禁：让 Agent 做代码修改，说明 Reflection 会生成 pytest 验收 case，不通过则回流继续执行。

对外讲解时应明确：这是本地单用户 Agent Runtime MVP，核心价值在于 Agent 工程治理，而不是完整生产平台。

仓库内的 GitHub Actions 会自动执行 lint、类型检查、测试覆盖率、Agent eval 和 Python 包构建；覆盖率低于 80% 时门禁失败。

## 版本号规则

- 展示版本号使用 `vYYYYMMDD.HHMM`，例如 `v20260702.1644`。
- Python 包元数据 `pyproject.toml` 使用 PEP 440 合法格式 `YYYYMMDD.HHMM`。
- 后续更新版本号时，同步更新 `pyproject.toml`、`src/manus_mini/__init__.py` 和版本测试。
