# 查询驱动项目检索实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用轻量 Repo Map、批量代码搜索和 Top-K 证据读取替代每轮全仓预扫描。

**Architecture:** Planner/ReAct 只获取浅层项目地图；代码定位通过支持多查询的 `search_code` 完成；ReAct 在工具准备阶段执行搜索次数和不同文件读取预算。缓存按浅层目录和 manifest 元数据失效，不读取源码正文。

**Tech Stack:** Python 3.12、ripgrep JSON 输出、现有 ToolRegistry、pytest。

---

### Task 1: 轻量 Repo Map

- [ ] 新增失败测试，证明提示词构建不会读取深层源码符号。
- [ ] 将 `project_index.py` 改为浅层目录、manifest 和固定入口探测。
- [ ] 验证单文件源码内容变化不会触发全仓符号重建。

### Task 2: 多查询 search_code

- [ ] 新增失败测试，覆盖 `queries`、查询归属、固定字符串和正则搜索。
- [ ] 使用 `rg --json` 实现结构化搜索，并保留 Python fallback。
- [ ] 增加候选结果评分和稳定排序。

### Task 3: 检索预算

- [ ] 新增失败测试，覆盖最多两次搜索和六个不同文件读取。
- [ ] 在 ReAct 工具准备阶段拒绝超预算调用并生成明确 observation。
- [ ] 更新提示词，要求首轮批量搜索、Top-K 并行读取和单层扩展。

### Task 4: 回归验证

- [ ] 更新 README 和技术设计文档。
- [ ] 运行 `pytest -q`。
- [ ] 运行 `ruff check src tests` 和 `git diff --check`。
