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

## 本轮新增/调整测试

- [tests/test_runtime.py](/Users/liyong/Desktop/ai-manus/tests/test_runtime.py)
  - 增加 `test_runtime_fallback_answers_interview_engineering_questions`，覆盖模型配置、日志产物、上下文压缩、长期记忆、搜索失败和历史会话查看六类面试高频追问。

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

- `pytest -q`：506 passed
- `ruff check src tests evals`：通过
- `mypy`：30 个源码文件无错误
- `python evals/run_evals.py`：9/9 通过
- `pytest --cov=manus_mini --cov-report=term-missing`：84.82%（门禁 80%）
- `python -m build`：沙箱内因 DNS/PyPI 访问失败，使用外部权限重跑后通过，生成 sdist 和 wheel
