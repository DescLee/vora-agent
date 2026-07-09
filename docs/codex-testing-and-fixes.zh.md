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

### 13. `run_bash` 的重定向写文件命令会绕过确认流

#### 现象

- 上一轮只拦截了 `sed -i`、`perl -pi`、`echo/printf > file` 等常见写入形式。
- 但真实代码修改中，模型也常用 `cat <<EOF > file`、`python ... > file` 或其他命令重定向到工作区文件。
- 这类命令旧实现不会识别为工作区文件修改，可能直接执行并改写文件。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中扩展本地命令风险启发式。
- 新增对相对路径输出重定向的识别，例如 `> note.md`、`>> logs/result.txt`。
- 规则只针对相对路径，避免把 `/tmp/...` 这类非工作区输出路径误判为工作区文件修改。

#### 回归点

- `run_bash` 执行 `cat <<EOF > note.md` 这类重定向写入时，必须先进入确认流。
- 未确认前，目标文件内容不得被改写。

### 14. `run_bash` 修改生产代码时会绕过“先测试再改代码”的门禁

#### 现象

- 之前代码修改前置门禁只覆盖 `write_file`、`replace_in_file`、`append_file`。
- 如果模型改用 `run_bash` 通过重定向直接改写 `app.py`、`index.ts` 这类生产代码文件，即使已经进入确认流，确认后仍可能直接执行。
- 这意味着 shell 写代码路径没有复用“先准备并执行测试，再修改生产代码”的工程门禁。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中扩展代码修改前置校验。
- 对 `run_bash` / `run_temp_script` 中明显的相对路径重定向写入，先解析目标路径。
- 如果目标是生产代码文件，且当前任务还没有测试执行证据，则直接返回：
  - `CODE_CHANGE_REQUIRES_TEST_FIRST`

#### 回归点

- `run_bash` 通过 `> app.py` 这类方式改生产代码时，也必须先有测试执行证据。
- 未跑测试前，shell 路径不能成为绕过代码门禁的旁路。

### 15. `run_bash` 通过 `tee` 写文件时会绕过确认流

#### 现象

- 之前已经拦截了重定向写入和部分原地编辑命令，但 `tee note.md` / `tee -a note.md` 这类命令仍能直接写工作区文件。
- 这也是模型常见的 shell 写文件方式，尤其在拼接 `printf ... | tee file` 时很常见。
- 旧实现不会把这类命令识别成工作区文件修改，可能直接执行。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中扩展本地风险启发式。
- 新增对 `tee` / `tee -a` 写相对路径文件的识别。
- 仍然只拦截相对路径，避免误伤 `/tmp/...` 这类非工作区输出。

#### 回归点

- `run_bash` 执行 `printf 'x' | tee note.md` 时，必须先进入确认流。
- 未确认前，目标文件内容不得被改写。

### 16. `run_bash` 通过 `tee` 写生产代码时会绕过测试前置门禁

#### 现象

- 上一轮已经把 `tee note.md` 这类命令拦进确认流，但 `react.py` 里用于识别 shell 写代码目标路径的逻辑仍只覆盖 `> file` / `>> file`。
- 如果模型改用 `printf ... | tee app.py` 写生产代码，即使命令本身进入确认流，确认后仍可能直接执行。
- 这意味着 `tee` 路径没有复用“先测试再改生产代码”的工程门禁。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中扩展 `_shell_write_path()`。
- 除了重定向写入外，也会识别 `tee` / `tee -a` 的相对路径目标。
- 命中生产代码文件且没有测试执行证据时，继续返回：
  - `CODE_CHANGE_REQUIRES_TEST_FIRST`

#### 回归点

- `run_bash` 执行 `printf 'x' | tee app.py` 这类命令时，也必须先有测试执行证据。
- `tee` 不应成为绕过代码测试门禁的旁路。

### 17. `run_bash` 的原地编辑生产代码命令会绕过测试前置门禁

#### 现象

- 之前已经把 `sed -i` / `perl -pi` 纳入确认流，但 `react.py` 中用于识别 shell 写代码目标路径的逻辑还没覆盖这类原地编辑命令。
- 如果模型改用 `sed -i '' 's/.../.../' app.py` 或 `perl -pi -e '...' app.py` 修改生产代码，即使命令已经确认，仍可能直接执行。
- 这会让原地编辑命令成为绕过“先测试再改生产代码”门禁的另一条旁路。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中扩展 `_shell_write_path()`。
- 除了 `tee` 和重定向写入外，也会识别 `sed -i`、`perl -pi` 的目标文件路径。
- 命中生产代码文件且没有测试执行证据时，同样返回：
  - `CODE_CHANGE_REQUIRES_TEST_FIRST`

#### 回归点

- `run_bash` 执行 `sed -i ... app.py` 这类原地编辑生产代码命令时，也必须先有测试执行证据。
- `sed -i` / `perl -pi` 不应成为绕过代码测试门禁的旁路。

### 18. `manus-mini resume` 恢复不存在的会话时直接抛 Python 异常

#### 现象

- 按普通用户路径执行 `manus-mini resume missing-session --cwd <目录>` 时，旧实现直接让 `FileNotFoundError` 冒泡。
- 用户会看到 Python traceback，而不是清晰的 CLI 错误信息。
- 同类的 `remove` 子命令已经有友好错误提示，`resume` 行为不一致。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中捕获 `FileNotFoundError`。
- 缺失会话时输出：
  - `Error: session '<session_id>' not found.`
- 随后以 `SystemExit(1)` 退出，和 `remove` 的失败语义保持一致。

#### 回归点

- `manus-mini resume <missing>` 不应输出 Python traceback。
- 命令应打印友好错误，并以非 0 状态退出。

### 19. `run_bash` 通过 Python 写生产代码时会绕过测试前置门禁

#### 现象

- 本地风险启发式已经能把 `Path('app.py').write_text(...)` / `open('app.py', 'w')` 这类命令拦进确认流。
- 但确认后，`react.py` 的 shell 写代码目标解析还没有覆盖 Python 文件写入表达式。
- 如果模型通过 `python -c "Path('app.py').write_text(...)"` 修改生产代码，仍可能绕过“先测试再改代码”的门禁。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中扩展 `_shell_write_path()`。
- 新增识别：
  - `Path('file').write_text(...)`
  - `open('file', 'w'/'a')`
- 命中生产代码文件且没有测试执行证据时，同样返回：
  - `CODE_CHANGE_REQUIRES_TEST_FIRST`

#### 回归点

- `run_bash` 通过 Python 写入 `app.py` 这类生产代码文件时，也必须先有测试执行证据。
- Python 写文件表达式不应成为绕过代码测试门禁的旁路。

## 本轮新增/调整测试

- [tests/test_cli.py](/Users/liyong/Desktop/ai-manus/tests/test_cli.py)
  - 顶层 `--cwd` 兼容
  - 旧写法子命令参数兼容
  - `clear` 必须先确认再删除会话
  - `resume` 缺失会话时输出友好错误
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
  - `run_bash` 的重定向写文件命令必须进入确认流
  - `run_bash` 写生产代码时也必须先通过测试前置门禁
  - `run_bash` 的 `tee` 写文件命令必须进入确认流
  - `run_bash` 的 `tee` 写生产代码命令也必须先通过测试前置门禁
  - `run_bash` 的原地编辑生产代码命令也必须先通过测试前置门禁
  - `run_bash` 的 Python 写生产代码命令也必须先通过测试前置门禁
- [tests/test_prompt_tui.py](/Users/liyong/Desktop/ai-manus/tests/test_prompt_tui.py)
  - 英文 reasoning 在中文 TUI 中的展示收口

## 验证结果

本轮修复完成后，已执行：

```bash
pytest -q
```

结果：

- `357 passed`

并额外做了本地脚本级别验证，确认以下场景可正常返回：

- LLM 不可用时回答身份问题
- LLM 不可用时回答启动问题
- LLM 返回空字符串时，最终仍有可展示结果
- 普通行研问答不会误入写文件确认流程
- `web_search` 无结果时，最终答案会主动提示“未获取到有效搜索结果”
- `web_search` 执行失败时，最终答案也会主动提示“未获取到有效搜索结果”
- `replace_in_file` 不会再直接修改文件，而是先等待确认
- `run_bash` 中明显会改文件的命令会被拦到确认流
- `run_bash` 中重定向写入工作区文件的命令也会被拦到确认流
- `run_bash` 写生产代码时也不能绕过“先测试再改代码”的门禁
- `run_bash` 通过 `tee` 写生产代码时也不能绕过“先测试再改代码”的门禁
- `run_bash` 原地编辑生产代码时也不能绕过“先测试再改代码”的门禁
- `run_bash` 通过 Python 写生产代码时也不能绕过“先测试再改代码”的门禁
- 中文 TUI 不会再直接展示大段英文 reasoning

## 后续建议

本轮主要修了“启动链路可用性”和“异常情况下的回答收口”。后续还可以继续优化：

1. 项目简介类回答进一步压缩长度，减少 README 复述感。
2. 为规则兜底增加更多高频模板，如“查看历史会话”“恢复会话”“当前项目边界”。
3. 在 TUI 欢迎区增加启动前自检摘要，例如配置来源、存储目录和模型连通性状态。
