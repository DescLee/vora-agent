# Vora 项目介绍

> 生成日期：2026 年 7 月 1 日

---

## 一、项目概述

**Vora** 是一个面向面试展示的本地 AI Agent Runtime 项目，版本号 `0.1.0`。它的核心定位是**展示现代 Agent 工程的系统化落地能力**，而非堆叠功能。

项目使用 Python 3.12+，依赖 `prompt_toolkit`、`pydantic`、`rich` 等库构建本地交互、工具编排和数据模型。

---

## 二、核心定位

- **不是**简单的单轮 LLM 问答。
- **不是** Web 页面应用（第一版不做 Web）。
- **而是**一个围绕**会话、目标、工具、状态、权限和反馈循环**搭建的工程系统。
- 旨在向面试官或技术评审者证明：候选人理解 Agent 应用从自然语言目标到可交付产物的完整工程链路。

---

## 三、三类核心能力

| 能力 | 描述 | 示例 |
|------|------|------|
| 📚 **资料调研** | 读取本地资料、提炼要点、生成 Markdown 报告 | `阅读 docs 目录，生成项目背景报告` |
| 💻 **本地项目助手** | 扫描项目结构、读取文件、生成修改方案（需用户确认后才写入） | `分析当前项目结构，建议如何添加配置模块` |
| 🤖 **任务自动化** | 整理本地文本/文件，生成摘要、清单或结构化结果 | `把 inbox 目录下的文本整理成 todo.md` |

---

## 四、三层 Agent Loop（核心架构亮点）

Vora 不是一次 LLM 调用，而是由三层循环组成的工程系统：

```
外层：工程兜底循环（Engineering Guard Loop）
  ├── 控制最大步数和 token 预算
  ├── 处理工具超时、重试、异常降级
  │
  └── 中层：Reflection 质量反馈循环（Reflection Loop）
      ├── 评判草稿质量，决定 accept / local_update / regenerate / replan
      │
      └── 内层：ReAct 工具循环（ReAct Tool Loop）
          ├── LLM 思考 → 返回 tool_calls
          ├── Agent 执行工具 → 返回观察结果
          └── LLM 继续思考 → 直到产出草稿
```

**默认限制参数：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_engineering_steps` | 12 | 外层工程兜底循环最大步数 |
| `max_react_iterations` | 8 | 单个步骤内 ReAct 工具循环最大次数 |
| `max_reflection_rounds` | 5 | Reflection 质量反馈循环最大次数 |
| `max_tool_retries` | 2 | 单个工具调用最大重试次数 |
| `max_estimated_tokens` | 128,000 | 上下文预算上限 |

---

## 五、技术架构

### 总体架构图

```
CLI / Interactive App
    ↓
SessionManager
    ↓
AgentRuntime
    ├── Planner          — 把用户目标转成初始计划
    ├── Executor         — 根据计划执行工具
    │   └── ToolScheduler — 分析工具依赖，并行/串行调度
    ├── Observer         — 把工具结果转成 LLM 可理解的观察
    ├── Reflector        — 评判结果质量，决定下一步
    ├── Reporter         — 生成最终 Markdown 产物
    ├── MemoryManager    — 长期记忆的写入、检索、删除
    ├── ContextCompressor — 长会话自动压缩上下文
    └── EventLogger      — 结构化事件日志
```

### 目录结构

```
src/vora/
    cli.py               — CLI 入口
    prompt_tui.py        — resume 交互恢复界面
    runtime.py           — Agent 主循环
    models.py            — 核心数据模型（SessionState, TaskState 等）
    llm.py               — LLM 抽象（MockLLMClient / OpenAICompatibleClient）
    session.py           — 会话管理器
    memory.py            — 长期记忆管理器
    context.py           — 上下文构建与压缩
    planner.py           — 计划生成器
    executor.py          — 工具执行器
    scheduler.py         — 工具并行调度器
    observer.py          — 观察者
    reflector.py         — 质量评判器
    reporter.py          — 报告生成器
    logging.py           — 事件日志
    tools/               — 工具协议与实现
        base.py          — 工具基类 & ToolSpec
        registry.py      — 工具注册中心
        file_tools.py    — 文件读写工具
        research_tools.py — 调研工具
        code_tools.py    — 代码分析工具
        automation_tools.py — 自动化工具
```

---

## 六、核心数据模型

| 模型 | 作用 |
|------|------|
| `SessionState` | 会话状态：消息历史、当前任务、产物、待确认动作 |
| `Message` | 消息单元：支持 user / agent / system / tool 四种角色 |
| `TaskState` | 任务状态：目标、当前步骤、观察、错误、循环限制 |
| `LoopLimits` | 三层循环的限制参数 |
| `PlanStep` | 计划步骤：描述、意图、状态 |
| `ToolSpec` | 工具规范：名称、描述、入参 schema、风险等级 |
| `ToolResult` | 工具执行结果 |
| `MemoryItem` | 长期记忆条目：作用域、类型、内容、置信度 |
| `CompressionSnapshot` | 压缩快照：记录了哪些消息被压缩、摘要内容、保留事实 |
| `ContextBundle` | 模型输入上下文：当前消息、最近消息、记忆、压缩摘要、产物 |

---

## 七、关键设计

### 7.1 工具并行调度

LLM 一轮返回的多个 `tool_calls` 不会默认串行执行，而是经过依赖分析：

- **无数据依赖、只读工具** → 并行执行
- **有数据依赖或写入冲突** → 串行执行
- **需要用户确认的写入工具** → 单独成批，确认后才执行

### 7.2 文件写入确认

所有真实文件修改前必须展示变更摘要，等待用户输入 `y` 后才执行。其他输入默认取消该操作，Agent 继续反思替代方案。

### 7.3 长期记忆

- 存储位置：`.vora/memory.db`
- 写入条件：用户明确偏好、项目摘要、产物摘要、明确决策
- 不写入：未确认猜测、临时错误、密钥/隐私数据
- 每轮最多注入 5 条记忆

### 7.4 上下文压缩

当上下文接近模型预算的 70% 时自动触发压缩：

- 最近 6 条消息保留原文
- 旧消息压缩成摘要（保留目标、约束、决策、产物变化）
- **工具调用消息必须成组保留或成组删除**，防止孤儿 `tool_call_id`
- 压缩后运行完整性校验

### 7.5 权限与安全

- 默认只能访问 `cwd` 内文件
- 路径逃逸（`../`）被拒绝
- 写入工具必须 preview + 确认
- 默认不执行 shell 命令
- 支持 `--dry-run` 模式

---

## 八、运行方式

### 安装

```bash
pip install -e ".[dev]"
```

### 配置（.env 文件）

```env
LLM_PROVIDER=mock                          # mock / openai-compatible
LLM_BASE_URL=                              # e.g. http://localhost:1234/v1
LLM_API_KEY=
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT_SECONDS=30
```

### 启动

```bash
vora run "总结一下当前项目" --cwd .
vora list --cwd .
vora resume <session_id> --cwd .
```

### 参数选项

| 参数 | 说明 |
|------|------|
| `--cwd <path>` | 指定工作目录 |
| `--max-steps <n>` | 工程兜底最大步数 |
| `--max-react <n>` | ReAct 循环最大次数 |
| `--max-reflect <n>` | Reflection 循环最大次数 |
| `--max-tool-retries <n>` | 工具重试次数 |
| `--mode <auto\|confirm>` | 执行模式 |
| `--dry-run` | 只展示计划，不修改文件 |

---

## 九、交互恢复布局

```
+------------------------------------------------------------+
| Vora                          cwd: ./project         |
+-------------------------+----------------------------------+
| 对话区                  | 当前产物                         |
| 用户/Agent 消息         | report.md / patch 预览            |
| 工具调用摘要            |                                  |
+-------------------------+----------------------------------+
| 状态栏: step 3/8 | react 2/5 | reflect 1/3 | context 68%   |
+------------------------------------------------------------+
| 输入区: 继续输入你的要求...                                |
+------------------------------------------------------------+
```

---

## 十、第二版演进方向

| 方向 | 说明 |
|------|------|
| 权限系统 | 按工具、路径、风险等级分级授权 |
| 命令工具 | 白名单命令沙箱，默认超时 |
| 任务持久化 | 支持断点恢复 |
| 评测集 | 固定任务集、自动评分 |
| 多模型策略 | 规划/执行/总结模型分离 |
| Trace 可视化 | 从 JSONL 生成运行时间线 |
| 向量记忆 | 语义检索、置信度、冲突检测 |
| 人工介入 | 歧义过大时主动询问 |

---

## 十一、一句话总结

> **Vora V1 是一个小而完整的本地 Agent Runtime，它证明了 Agent 不是一次 LLM 调用，而是围绕会话、目标、工具、状态、权限和三层反馈循环搭建的工程系统。**
