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

### 8. `replace_in_file` 会绕过写入确认流

#### 现象

- 在真实测试中，用户要求“把 `note.md` 里的 `old line` 改成 `new line`”时，Agent 可能直接调用 `replace_in_file` 完成修改。
- 修改发生前没有进入 `pending_confirmation`，文件内容被直接改写。
- 这与项目声明的“写入前需人工确认”不一致。

#### 修复

- 在 [src/manus_mini/tools/file_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/file_tools.py) 中将 `replace_in_file` 纳入确认流。
- 现在 `replace_in_file` 会像 `write_file` / `append_file` 一样先生成 diff 预览，再等待用户确认。

#### 回归点

- `replace_in_file` 修改已有文件时，必须进入 `waiting_confirmation`。
- 未确认前，目标文件内容不得发生变化。

### 9. `run_bash` 中明显会改文件的命令未被识别为高风险写入

#### 现象

- 在真实测试中，模型可能绕过文件工具，改用 `run_bash` 执行诸如 `sed -i`、重定向写文件等命令直接修改工作区文件。
- 旧实现只依赖 LLM 风险裁判或通用黑名单，无法稳定识别这类“本地文件原地修改”命令。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中增加本地启发式风险识别。
- 目前会优先拦截这类明显的工作区文件修改命令并进入确认流，例如：
  - `sed -i`
  - `perl -pi`
  - `printf/echo > file` 或 `>> file`
  - 常见 Python 文件写入调用
- 本地启发式先于 LLM 风险判断生效，避免“命令已执行但才发现是写入”的问题。

#### 回归点

- `run_bash` 执行明显会修改工作区文件的命令时，必须要求确认。
- 未确认前，目标文件内容不得被改写。

### 10. 中文 TUI 中的“推理”内容可能夹杂整段英文

#### 现象

- 在真实测试中，TUI 的 `执行过程 -> 推理` 区域前半段是中文，后半段会突然出现整段英文 reasoning。
- 例如界面里会直接展示 `Now I have a good overview of the project...` 这类英文思考内容。
- 这会破坏中文界面的连贯性，也容易把模型中间态思考直接暴露给用户。

#### 修复

- 在 [src/manus_mini/prompt_tui_formatting.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/prompt_tui_formatting.py) 中收口 `reasoning_content` 的展示逻辑。
- 对 `- 推理:` 和 `规划理由:` 共用同一套摘要格式化。
- 当 reasoning 明显以英文为主且不含中文时，不再原样展示英文内容，而是改为中文提示：
  - `模型已生成推理内容，因包含较多英文，界面中不直接展示原文。`

#### 回归点

- 中文 TUI 中不应再直接显示大段英文 reasoning 原文。
- `推理` 和 `规划理由` 两处展示应保持一致的中文收口行为。

### 11. `manus-mini clear` 会先删除会话，再询问用户是否确认

#### 现象

- 按普通用户路径执行 `manus-mini clear --cwd <目录>` 时，旧实现会先调用 `clear_all()` 删除全部会话。
- 然后才弹出确认提示；即使用户输入 `n` 或直接取消，会话其实已经被删掉了。
- 这会导致 CLI 确认流形同虚设，属于明显的数据删除顺序错误。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中调整 `clear` 子命令流程。
- 先用 `list_sessions()` 统计当前会话数量，仅用于展示确认提示。
- 只有用户明确确认后，才真正执行 `clear_all()` 和日志清理。

#### 回归点

- 用户拒绝 `manus-mini clear` 后，已有会话必须仍然存在。
- 只有确认通过后，CLI 才能真正删除会话和对应日志。

### 12. 联网搜索失败时，最终行研回答未提示证据不足

#### 现象

- 旧逻辑只处理 `web_search` 成功执行但返回 0 结果的情况。
- 如果搜索工具本身失败，例如超时、网络错误或搜索服务异常，Agent 仍可能直接生成行研摘要。
- 用户看到完整答案时，无法区分“已联网核实”还是“搜索失败后基于已有知识整理”。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中将搜索收口条件改为“本轮没有任何有效搜索结果”。
- 只要本轮调用过 `web_search`，但没有拿到有效结果，就在最终答案前追加证据不足提示。
- 如果存在至少一次有效搜索结果，则不额外添加该提示，避免误伤正常搜索场景。

#### 回归点

- `web_search` 返回 0 结果时，最终答案必须提示证据不足。
- `web_search` 执行失败时，最终答案也必须提示证据不足。

## 本轮新增/调整测试

- [tests/test_cli.py](/Users/liyong/Desktop/ai-manus/tests/test_cli.py)
  - 顶层 `--cwd` 兼容
  - 旧写法子命令参数兼容
  - `clear` 必须先确认再删除会话
- [tests/test_llm.py](/Users/liyong/Desktop/ai-manus/tests/test_llm.py)
  - 原始工具调用 DSL 收口
- [tests/test_logging.py](/Users/liyong/Desktop/ai-manus/tests/test_logging.py)
  - 用户目录不可写时路径回退
- [tests/test_runtime.py](/Users/liyong/Desktop/ai-manus/tests/test_runtime.py)
  - fallback 高价值回答
  - 空结果保护
  - 行研问答默认不落文件
  - 搜索 0 结果时增加证据不足提示
  - 搜索失败时增加证据不足提示
  - `replace_in_file` 必须进入确认流
  - `run_bash` 的原地文件修改命令必须进入确认流
- [tests/test_prompt_tui.py](/Users/liyong/Desktop/ai-manus/tests/test_prompt_tui.py)
  - 英文 reasoning 在中文 TUI 中的展示收口

## 验证结果

本轮修复完成后，已执行：

```bash
pytest -q
```

结果：

- `350 passed`

并额外做了本地脚本级别验证，确认以下场景可正常返回：

- LLM 不可用时回答身份问题
- LLM 不可用时回答启动问题
- LLM 返回空字符串时，最终仍有可展示结果
- 普通行研问答不会误入写文件确认流程
- `web_search` 无结果时，最终答案会主动提示“未获取到有效搜索结果”
- `web_search` 执行失败时，最终答案也会主动提示“未获取到有效搜索结果”
- `replace_in_file` 不会再直接修改文件，而是先等待确认
- `run_bash` 中明显会改文件的命令会被拦到确认流
- 中文 TUI 不会再直接展示大段英文 reasoning

## 后续建议

本轮主要修了“启动链路可用性”和“异常情况下的回答收口”。后续还可以继续优化：

1. 项目简介类回答进一步压缩长度，减少 README 复述感。
2. 为规则兜底增加更多高频模板，如“查看历史会话”“恢复会话”“当前项目边界”。
3. 在 TUI 欢迎区增加启动前自检摘要，例如配置来源、存储目录和模型连通性状态。
