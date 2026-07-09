# Codex 测试与修复记录

本文档记录本轮通过 Codex 对 `manus-mini` 进行真实运行测试后发现并修复的问题，便于后续回归验证、面试演示和继续迭代。

## 测试范围

- CLI 启动链路
- TUI 启动前参数解析
- LLM 工具调用结果收口
- 用户目录不可写时的项目存储路径
- 模型不可用时的规则兜底
- 最终结果为空字符串时的收口保护

## 已修复问题

### 1. CLI 默认启动命令与文档示例不一致

#### 现象

- 直接执行 `manus-mini --cwd .` 时，旧实现会把 `.` 误判为子命令参数，报 `invalid choice`。
- README 中的启动示例和实际 CLI 解析行为不一致，容易误导首次使用者。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中增加顶层全局参数兼容。
- 同时保留 `manus-mini tui --cwd .` 和 `manus-mini list --cwd .` 这类原有子命令写法。
- 在 [README.md](/Users/liyong/Desktop/ai-manus/README.md) 中将显式启动命令改为 `manus-mini tui --cwd .`，并保留兼容示例。

#### 回归点

- `manus-mini --cwd .`
- `manus-mini tui --cwd .`
- `manus-mini list --cwd .`

### 2. 模型可能将原始工具调用 DSL 直接暴露给用户

#### 现象

- 在真实外部模型问答中，`怎么启动和使用？` 一度返回原始工具调用文本，如 `<｜｜DSML｜｜tool_calls>`、`invoke name="read_file"` 等。
- 这说明模型把中间态 DSL 塞进了普通 content，运行时此前没有拦截。

#### 修复

- 在 [src/manus_mini/llm.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/llm.py) 中增加对原始工具调用标记的检测。
- 命中后视为无效 LLM 输出，转入现有 rule fallback，而不是直接展示给用户。

#### 回归点

- 用户最终结果中不应再出现 `<｜｜DSML｜｜tool_calls>`、`invoke name=` 等原始 DSL 片段。

### 3. 用户主目录不可写时，运行时会在项目存储初始化阶段失败

#### 现象

- 默认日志、会话、产物、记忆路径位于 `~/.manus-mini`。
- 受限环境或沙箱环境下，用户主目录可能不可写，导致初始化过程直接抛错。

#### 修复

- 在 [src/manus_mini/logging.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/logging.py) 中增加回退逻辑。
- 如果默认用户目录不可写，则自动回退到项目内 `.manus-mini`。

#### 回归点

- 用户目录不可写时，项目仍能完成会话、日志和产物路径初始化。

### 4. 模型不可用时，规则兜底回答过于空泛

#### 现象

- 当外部 LLM 不可用时，原 fallback 一般只输出：
  - `已使用规则兜底生成草稿`
  - `兜底原因`
  - `当前目标`
- 对高频问题如“你是谁”“怎么启动和使用”“项目是做什么的”帮助很小。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中扩展规则兜底：
  - 身份问题直接回答 `我是 manus-mini...`
  - 启动问题直接给安装、配置、启动和会话命令
  - “模型不可用”类问题直接解释兜底行为
  - 项目简介类问题给最小可用摘要

#### 回归点

- LLM 不可用时，以下问题仍需得到可读回答：
  - `你好，你是谁？`
  - `怎么启动和使用？`
  - `如果模型不可用，你会怎么表现？`

### 5. 最终结果可能为空字符串

#### 现象

- 在真实外部模型问答中，`如果模型不可用，你会怎么表现？` 曾出现空白回答。
- 这说明运行时在最终落消息前没有统一做“非空结果保障”。

#### 修复

- 在 [src/manus_mini/runtime.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/runtime.py) 中增加 `_ensure_non_empty_result`。
- 无论是正常 reflection 结果、循环上限收口，还是确认后继续执行的结果，只要最终字符串为空，就强制回退到规则 fallback。

#### 回归点

- 用户最终收到的结果不得为空字符串。

### 6. 普通行研问答会误触发 `write_file` 流程

#### 现象

- 在真实测试中，用户只是要求“给一个简短行研摘要”，Agent 却直接尝试 `write_file` 生成 `docs/ai-agent-framework-landscape.md`。
- 由于写文件工具要求确认，普通问答被错误地带入“等待确认写入”流程，偏离用户预期。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加报告类写入前置条件。
- 对“行研/调研/摘要/报告”这类普通问答请求，默认要求 **直接在对话中回答**。
- 只有当用户明确提出“保存到文件 / 写入文件 / 生成文件”等意图时，才允许 `write_file` 落文档。

#### 回归点

- “请给我一份 AI Agent 框架行研摘要” 这类请求不应触发 `pending_confirmation`。
- 普通报告问答应优先返回聊天答案，而不是进入写文件确认流。

### 7. 联网搜索没有结果时，最终行研回答未提示证据不足

#### 现象

- 在真实测试中，`web_search` 连续返回 `No results found` 后，Agent 仍然直接生成完整行研摘要。
- 虽然答案在语言上可读，但没有明确告诉用户“本次没有拿到有效来源”，容易让人误以为内容已经过联网核实。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中增加搜索失败收口逻辑。
- 如果本轮 `web_search` 全部成功执行但结果都为 0，则在最终答案前自动加提示：
  - `本次联网搜索未获取到有效搜索结果`
  - `下面内容基于已有知识整理`

#### 回归点

- 搜索 0 结果时，最终回答必须显式提示来源不足，而不是直接伪装成已联网核实结论。

## 本轮新增/调整测试

- [tests/test_cli.py](/Users/liyong/Desktop/ai-manus/tests/test_cli.py)
  - 顶层 `--cwd` 兼容
  - 旧写法子命令参数兼容
- [tests/test_llm.py](/Users/liyong/Desktop/ai-manus/tests/test_llm.py)
  - 原始工具调用 DSL 收口
- [tests/test_logging.py](/Users/liyong/Desktop/ai-manus/tests/test_logging.py)
  - 用户目录不可写时路径回退
- [tests/test_runtime.py](/Users/liyong/Desktop/ai-manus/tests/test_runtime.py)
  - fallback 高价值回答
  - 空结果保护
  - 行研问答默认不落文件
  - 搜索 0 结果时增加证据不足提示

## 验证结果

本轮修复完成后，已执行：

```bash
pytest -q
```

结果：

- `343 passed`

并额外做了本地脚本级别验证，确认以下场景可正常返回：

- LLM 不可用时回答身份问题
- LLM 不可用时回答启动问题
- LLM 返回空字符串时，最终仍有可展示结果
- 普通行研问答不会误入写文件确认流程
- `web_search` 无结果时，最终答案会主动提示“未获取到有效搜索结果”

## 后续建议

本轮主要修了“启动链路可用性”和“异常情况下的回答收口”。后续还可以继续优化：

1. 项目简介类回答进一步压缩长度，减少 README 复述感。
2. 为规则兜底增加更多高频模板，如“查看历史会话”“恢复会话”“当前项目边界”。
3. 在 TUI 欢迎区增加启动前自检摘要，例如配置来源、存储目录和模型连通性状态。
