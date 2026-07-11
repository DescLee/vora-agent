# vora

本地 Agent Runtime

`vora` 是一个本地 Agent Runtime。项目重点不是复刻任何现有 Agent 产品，而是实现一个可运行、可测试、可观察、带安全边界的 Agent 工程骨架；它可以用于面试或技术评审中讲解 Agent 工程能力，但项目本身定位仍是本地 Agent Runtime。

## 项目定位

当前版本重点体现：

- Agent 运行编排：`Runtime -> Planner -> ReAct -> ToolScheduler -> Executor -> Observer -> Reflection -> Reporter`
- 工具调用治理：工具 schema、批次调度、依赖处理、文件读写直执行策略、命令风险判断。
- Skills 能力包：按任务触发项目分析、代码修改、面试演示等流程约束，并收窄可用工具范围。
- MCP 配置管理：通过 CLI 管理项目级或用户级 MCP server 配置，为后续接入 MCP 工具适配层预留入口。
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
| Skills 能力包 | 已实现 | 支持内置、本地项目和用户全局 Skill；命中后注入 Planner/ReAct，并限制工具候选范围。 |
| MCP 配置管理 | 已实现 | `vora mcp list/add/remove` 可管理 MCP server 配置；当前版本先保存配置，尚未动态注入 MCP 工具。 |
| 文件工具 | 已实现 | list/read/write/replace/append/mkdir；`read_file`、`write_file`、`replace_in_file` 按用户要求直接执行，写入保留路径限制、diff 记录和 dry-run 不落盘。 |
| 命令工具 | 已实现 | bash/temp script，含禁用模式、风险判断、确认和超时。 |
| 长期记忆 | 已实现 | SQLite 存储，敏感信息过滤，关键词检索。 |
| 会话持久化 | 已实现 | list/resume/delete/clear，支持中断后修复 tool message。 |
| 上下文压缩 | 已实现 | 用户消息后和 LLM 返回后同步压缩；按 50/70/90 阈值压缩工具输出、摘要历史、强制截断；压缩时保持 assistant/tool exchange 成组完整。 |
| 启动自检 | 已实现 | `vora doctor` 可检查本地存储路径、会话数量和 LLM 配置完整性。 |
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

配置读取顺序：环境变量、当前运行目录 `.env`、用户级 `~/.vora/.env`、源码根目录 `.env`。

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
vora
vora doctor --cwd .
vora run "总结一下当前项目" --cwd .
```

常用参数：

```bash
vora --cwd . --max-steps 3 --max-react 99 --max-reflect 3 --dry-run
vora doctor --cwd .
vora run "总结一下当前项目" --cwd . --max-steps 3 --max-react 99 --max-reflect 3 --dry-run
vora list
vora resume <session_id> --cwd . --max-steps 3 --max-react 99 --max-reflect 3 --dry-run
vora mcp list --cwd .
vora skills list --cwd .
```

## MCP 配置

MCP 命令用于管理 server 配置，默认写入当前项目隔离存储；加 `--global` 可写入用户级 `~/.vora/mcp.json`。

```bash
vora mcp list --cwd .
vora mcp add filesystem --command npx --arg -y --arg @modelcontextprotocol/server-filesystem --cwd .
vora mcp remove filesystem --cwd .
```

当前版本只完成配置注册和 CLI 管理，运行时还不会把 MCP server 动态转换成 Agent 工具；后续接入点应放在 ToolRegistry/MCP adapter 层。

## Skills

Skill 是高于工具的一层任务能力包，用来描述触发词、执行指令、建议工具范围和验收标准。工具仍由 `ToolRegistry` 执行；Skill 只影响 Planner/ReAct 的行为约束和传给模型的工具候选列表。

加载顺序：

1. 内置 Skill：`project-analysis`、`code-change`、`interview-demo`
2. 当前项目：`./skills/*`
3. 用户全局：`~/.vora/skills/*`

新增项目级 Skill：

```bash
mkdir -p skills/project-analysis
```

也可以通过 CLI 从已有 Skill 目录复制：

```bash
vora skills list --cwd .
vora skills add ./skills/project-analysis --cwd .
vora skills remove project-analysis --cwd .
```

`skills/project-analysis/skill.json`：

```json
{
  "name": "project-analysis",
  "description": "分析本地项目结构、工程质量、测试体系和面试讲解点。",
  "triggers": ["项目分析", "架构", "面试", "工程质量"],
  "tool_allowlist": ["list_files", "read_file", "run_bash"],
  "acceptance": ["引用具体文件", "说明设计取舍", "指出边界和风险"]
}
```

`skills/project-analysis/instructions.md`：

```md
先看 README、pyproject、src、tests、docs。
输出时按架构、Agent 能力、工程质量、测试保障、风险和下一步分类。
面试场景下要强调设计取舍和工程边界，不要把项目说成完整生产平台。
```

## 验证

```bash
pytest -q
ruff check src tests evals
mypy
pytest --cov=vora --cov-report=term-missing
python evals/run_evals.py
```

## 项目讲解建议

推荐展示三条路径：

1. 项目分析：让 Agent 扫描当前项目并生成结构化总结。
2. 文件修改审计：让 Agent 修改一个文件，展示 diff preview；`read_file`、`write_file`、`replace_in_file` 按用户要求直接执行。
3. 代码门禁：让 Agent 做代码修改，说明 Reflection 会生成 pytest 验收 case，不通过则回流继续执行。

对外讲解时应明确：这是本地单用户 Agent Runtime MVP，核心价值在于 Agent 工程治理，而不是完整生产平台。

仓库内的 GitHub Actions 会自动执行 lint、类型检查、测试覆盖率、Agent eval 和 Python 包构建；覆盖率低于 80% 时门禁失败。

## 版本号规则

- 展示版本号使用 `vYYYYMMDD.HHMM`，例如 `v20260702.1644`。
- Python 包元数据 `pyproject.toml` 使用 PEP 440 合法格式 `YYYYMMDD.HHMM`。
- 后续更新版本号时，同步更新 `pyproject.toml`、`src/vora/__init__.py` 和版本测试。
