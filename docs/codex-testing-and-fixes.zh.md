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

### 10. 中文 TUI 的英文 reasoning 被固定提示替代

#### 现象

- 旧实现检测到较长英文 reasoning 后，只展示固定中文占位提示。
- 这会丢失模型实际返回的推理摘要，不利于调试和观察 Agent 的执行过程。

#### 修复

- 在 [src/manus_mini/prompt_tui_formatting.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/prompt_tui_formatting.py) 中移除按语言隐藏 reasoning 的逻辑。
- 中文和英文 reasoning 均按同一规则展示；超过 240 字符时仍会截断，避免撑满界面。

#### 回归点

- 英文 reasoning 应直接显示实际内容，不再替换为固定占位提示。
- 长 reasoning 仍应保持 240 字符截断保护。

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

### 20. 复合 shell 命令只检查第一个写入目标，后续生产代码写入会绕过门禁

#### 现象

- `_shell_write_path()` 之前只返回第一个识别到的写入路径。
- 如果模型在同一条 `run_bash` 里先写测试文件，再写生产代码，例如先 `> tests/test_app.py` 再 `> app.py`，门禁只看到测试路径。
- 因为测试路径不属于生产代码，后续 `app.py` 写入会绕过“先测试再改代码”的门禁。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中把 shell 写入路径解析从单路径改成多路径。
- `_shell_write_paths()` 会收集命令中所有可识别写入目标。
- 只要任一目标是生产代码文件，且没有测试执行证据，就返回：
  - `CODE_CHANGE_REQUIRES_TEST_FIRST`

#### 回归点

- 同一条 `run_bash` 同时写测试文件和生产代码文件时，也必须先有测试执行证据。
- 测试文件写入不应掩盖后续生产代码写入。

### 21. 普通行研问答可通过 `run_bash` 写报告文件，绕过“默认对话内回答”约束

#### 现象

- 之前普通行研/报告问答已经会拒绝 `write_file` / `replace_in_file` / `append_file` 的非显式文件输出。
- 但如果模型改用 `run_bash` 通过重定向写 `docs/report.md`，报告写入前置条件没有覆盖 shell 写文件路径。
- 用户只是要“给我一份行研摘要”时，Agent 仍可能直接落文件，偏离“默认在对话内回答”的预期。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中扩展报告写入前置条件。
- 对 `run_bash` / `run_temp_script` 中可识别的写文件路径，同样应用：
  - `REPORT_WRITE_REQUIRES_EXPLICIT_REQUEST`
- 只有用户明确要求“保存到文件 / 写入文件 / 生成文件”等场景，才允许报告类任务落文件。

#### 回归点

- 普通行研/报告问答不应通过 shell 命令写报告文件。
- `run_bash` 不应成为绕过“默认对话内回答”的旁路。

### 22. 等待写入确认时，普通消息会绕过待确认状态并开启新任务

#### 现象

- 当会话存在 `pending_confirmation` 时，用户如果没有输入 `确认` / `取消`，而是继续发普通问题，旧实现会继续调用 runtime 开启新任务。
- 这会让待确认写入悬而未决，同时新任务继续推进，用户容易误以为上一项修改已被取消或已处理。
- 对真实 TUI 使用来说，这会造成确认流状态混乱。

#### 修复

- 在 [src/manus_mini/session.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/session.py) 中收紧待确认状态处理。
- 如果存在待确认写入，除 `确认` / `取消` 和已有内置指令外，普通消息不会进入 runtime。
- 系统会提示：
  - `当前有待确认的写入操作，请先输入 \`确认\` 或 \`取消\`，再继续新的请求。`

#### 回归点

- 待确认状态下，普通消息不能开启新任务。
- 待确认项必须保持不变，直到用户明确确认或取消。

### 23. 报告类请求中“写到/输出到文件”会被误判为非显式落文件

#### 现象

- 之前报告类任务只有“保存到 / 写入文件 / 生成文件”等少数词会被识别为显式文件输出。
- 用户说“写到 `docs/report.md`”或“输出到某文件”时，语义上已经明确要求落文件。
- 旧实现仍可能返回 `REPORT_WRITE_REQUIRES_EXPLICIT_REQUEST`，导致用户明确要求保存文件时也不能进入正常写入确认流。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中扩展显式文件输出意图词。
- 新增识别：
  - `写到`
  - `输出到`
  - `放到`
  - `输出成`
- 明确落文件时仍会进入写入确认流，而不是直接写入。

#### 回归点

- “写到 `docs/report.md`”应允许报告任务进入写入确认流。
- 普通行研/报告问答没有明确落文件时，仍默认在对话内回答。

### 24. 取消/失败任务结果会被误当作已有产物注入下一轮

#### 现象

- 用户拒绝待确认写入后，旧任务会被标记为失败，并记录结果 `用户拒绝了待确认写入。`
- 下一轮普通问答开始时，runtime 只判断旧任务存在 `result`，会把失败结果作为 `已有产物` 注入上下文。
- 这会污染后续普通问答或代码修改任务，让模型误以为取消信息是上一轮有效产出。

#### 修复

- 在 [src/manus_mini/runtime.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/runtime.py) 中收紧已有产物注入条件。
- 只有旧任务 `status == "done"` 且存在结果时，才注入 `已有产物`。
- 失败、取消、等待确认等非完成状态不会再作为产物进入下一轮上下文。

#### 回归点

- 失败任务结果不会被注入为 `已有产物`。
- 已完成任务的结果仍可作为上一轮产物提供给后续任务。

### 25. 搜索有结果但网页正文全抓取失败时，回答缺少来源核实提示

#### 现象

- 行研/报告任务里，Agent 可能先通过 `web_search` 找到搜索结果，再用 `fetch_webpage` 读取正文。
- 旧实现只看 `web_search` 是否有结果，不看后续 `fetch_webpage` 是否全部失败。
- 当搜索返回了 URL，但网页正文全都读取失败时，最终回答仍可能使用“根据联网资料”这类口吻，容易让用户误以为已完成网页来源核实。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中补充最终回答收口判断。
- 如果 `web_search` 有有效结果，但后续 `fetch_webpage` 尝试全部失败，则在最终答案前增加提示：
  - `本次联网搜索虽返回了搜索结果，但页面内容读取失败`
- 仅在“搜索有效且抓取全失败”时触发，不影响已有的“搜索 0 结果 / 搜索直接失败”分支。

#### 回归点

- 搜索有结果但网页抓取全失败时，最终回答必须提示页面内容读取失败。
- 搜索 0 结果和搜索直接失败时，仍沿用原有免责声明逻辑。

### 26. `run_bash` 中 `Path(...).open('w')` 写代码可绕过测试前置门禁

#### 现象

- 代码修改任务里，runtime 会在真正改生产代码前要求先执行测试。
- 旧实现已拦截 `Path(...).write_text(...)`、`open(..., 'w')`、重定向、`tee`、`sed -i` 等常见写法。
- 但 `python -c "from pathlib import Path; Path('app.py').open('w').write(...)"` 这种写法没有被 `_shell_write_paths` 识别。
- 结果是 `run_bash` 能直接改动 `app.py`，绕过 `CODE_CHANGE_REQUIRES_TEST_FIRST`。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中补充 shell 写路径检测规则。
- 新增识别 `Path('...').open('w'/'a')` 这种 `pathlib` 写文件写法。
- 这样该类命令也会先触发“先测试再改代码”的门禁，而不是直接执行。

#### 回归点

- `Path(...).open('w')` 写生产代码时，必须被 `CODE_CHANGE_REQUIRES_TEST_FIRST` 拦住。
- 其他已覆盖的 shell 写代码门禁行为保持不变。

### 27. `run_bash` 中 `Path(...).write_bytes(...)` 写代码可绕过测试前置门禁

#### 现象

- 代码修改任务里，runtime 会在真正改生产代码前要求先执行测试。
- 旧实现虽然已识别 `Path(...).write_text(...)` 和 `Path(...).open('w')`，但没有覆盖 `Path(...).write_bytes(...)`。
- 因此 `python -c "from pathlib import Path; Path('app.py').write_bytes(...)"` 这种写法仍可直接改动生产代码，绕过 `CODE_CHANGE_REQUIRES_TEST_FIRST`。

#### 修复

- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中补充 shell 写路径检测规则。
- 新增识别 `Path('...').write_bytes(...)` 这种 `pathlib` 二进制写文件写法。
- 这样该类命令也会先触发“先测试再改代码”的门禁，而不是直接执行。

#### 回归点

- `Path(...).write_bytes(...)` 写生产代码时，必须被 `CODE_CHANGE_REQUIRES_TEST_FIRST` 拦住。
- 其他已覆盖的 `pathlib` 写代码门禁行为保持不变。

### 28. `touch` 创建文件可绕过确认流和测试前置门禁

#### 现象

- `touch app.py` 和 `Path('app.py').touch()` 都会创建文件或更新时间戳，属于工作区写入。
- 旧实现既没有把它们纳入 shell 写入确认流，也没有识别为生产代码修改目标。
- 模型可借此在未确认、未执行测试时创建生产代码文件。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中增加 `touch` 和 `Path(...).touch()` 的本地风险识别。
- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中解析 `touch` 的全部相对路径目标，并复用 `CODE_CHANGE_REQUIRES_TEST_FIRST` 门禁。
- 支持跳过 `touch -A/-d/-r/-t` 的选项参数，避免把参考时间参数误判成写入目标。

#### 回归点

- `touch` 或 `Path(...).touch()` 修改工作区文件前必须进入确认流。
- 目标包含生产代码且尚未执行测试时，必须被测试前置门禁拒绝。
- 同一命令同时创建测试文件和生产代码文件时，生产代码目标不能被遗漏。

### 29. 工具调用超时后，后台线程可能继续重试或执行 shell 子进程

#### 现象

- `Future.cancel()` 无法终止已经开始运行的 Python 线程。
- 旧实现返回 `TOOL_TIMEOUT` 后，工具线程仍可能继续运行；如果工具包含副作用，用户看到超时并不代表执行已经停止。
- shell 只依赖 `subprocess.run(timeout=...)`，对子进程树的回收语义不够明确。

#### 修复

- `Executor` 为每次工具调用创建协作式取消信号，超时、中断和批次超时时都会设置该信号。
- 工具重试前检查取消状态，避免超时后继续发起下一次执行。
- shell 工具改用独立进程组；超时或取消时先发送 `SIGTERM`，未退出再发送 `SIGKILL`，防止后台子进程残留。
- 明确边界：不响应取消信号的第三方 Python 工具仍无法在线程内安全强杀，生产环境必须使用进程或容器隔离。

#### 回归点

- shell 超时或取消后，其后台子进程不能继续创建文件。
- 工具收到取消信号后不能继续进入下一轮重试。

### 30. 工具和 LLM 瞬时失败重试没有退避

#### 现象

- 旧实现遇到 retryable tool error、HTTP 429/5xx、网络错误或超时后立即重试。
- 连续立即重试会放大上游压力，也没有遵循服务端 `Retry-After`。

#### 修复

- 工具重试增加指数退避，并在 trace 中记录 `delay_seconds`。
- LLM 重试增加指数退避和 jitter，支持 HTTP `Retry-After`，并限制单次等待最多 30 秒。
- 新增 `LLM_MAX_ATTEMPTS`、`LLM_RETRY_BACKOFF_SECONDS` 配置。

#### 回归点

- 429 响应携带 `Retry-After` 时优先采用服务端等待时间。
- 工具重试事件必须包含实际退避时长。

### 31. 工程质量检查只停留在文档，没有实际 CI

#### 现象

- 仓库虽然记录了 pytest、ruff 和 eval 命令，但没有 CI workflow。
- 缺少覆盖率阈值、类型检查和构建验证，无法阻止回归进入主分支。

#### 修复

- 新增 `.github/workflows/quality.yml`。
- 门禁包含 Ruff、mypy、pytest 分支覆盖率、Agent eval 和 Python 包构建。
- 覆盖率低于 80% 时失败，并上传 coverage XML 与 eval 报告。
- 为运行时依赖增加兼容版本边界，开发依赖增加 `pytest-cov`、`mypy` 和 `build`。

#### 回归点

- 本地 `mypy` 必须无错误。
- 全量测试覆盖率必须不低于 80%。
- `python -m build` 必须同时生成 wheel 和 sdist。

### 32. Eval 用例硬编码且缺少可归档报告

#### 现象

- 旧 eval 的元数据和执行函数全部硬编码在脚本中。
- 只能向 stdout 输出 JSON，不方便 CI 保存和人工审阅。

#### 修复

- 新增 `evals/cases.zh.json`，统一声明用例 ID、类别和中文目标。
- runner 校验重复 ID、缺失 runner 和未声明 runner，避免声明与实现漂移。
- 支持 `--json-report` 和 `--markdown-report`。
- 安全 eval 增加写入确认和危险命令拒绝，当前共 9 个产品约束用例。

#### 回归点

- 声明式用例必须与 runner 一一对应。
- JSON 和 Markdown 报告必须能在一次运行中同时生成。

### 33. 静态类型检查暴露多处潜在模型不一致

#### 现象

- Memory 的 `scope/kind` 使用普通字符串传递，与 Pydantic Literal 模型不一致。
- tool exchange 分组时没有向类型系统证明 `tool_call_id` 非空。
- runtime 的错误码和 memory 注入返回类型声明不准确。

#### 修复

- 提取 `AgentErrorCode`、`MemoryScope`、`MemoryKind` 类型别名。
- 修正 Runtime、MemoryManager、Context 和 ToolRegistry 的类型声明。
- 将 mypy 纳入 CI，当前 29 个源码文件检查通过。

#### 回归点

- `mypy` 输出必须为 `Success: no issues found`。
- 类型修复不能改变现有运行行为。

### 34. `run_bash` 中部分 `pathlib` 写文件方式会绕过确认流

#### 现象

- 作为真实用户从 shell 工具入口直接测试时，`Path('note.md').write_bytes(...)` 和 `Path('note.md').open('w').write(...)` 会直接写入工作区文件。
- 这两类命令没有触发 `COMMAND_REQUIRES_CONFIRMATION`，和项目声明的“写入前需人工确认”安全边界不一致。
- 运行时层已经覆盖了部分生产代码测试前置门禁，但工具层确认流本身仍存在旁路。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中扩展本地命令风险启发式。
- 新增识别：
  - `Path(...).write_bytes(...)`
  - `Path(...).open('w'/'a')`
- 命中后会先返回 `COMMAND_REQUIRES_CONFIRMATION`，未确认前不会执行 shell 命令。

#### 回归点

- `run_bash` 执行 `Path('note.md').write_bytes(...)` 时，必须先进入确认流。
- `run_bash` 执行 `Path('note.md').open('w').write(...)` 时，必须先进入确认流。
- 未确认前，目标文件不得被创建或改写。

### 35. `.env.test` 等环境变量文件变体可被文件工具直接写入

#### 现象

- 安全文档中的生产化权限模型建议将 `.env*` 作为 deny pattern。
- 旧实现只保护 `.env`、`.env.local`、`.env.production`、`.env.development` 等少数精确文件名。
- 真实测试中，`write_file`、`append_file`、`replace_in_file` 都能直接修改 `.env.test`。
- `.env.test` 在真实项目中常用于测试密钥或本地服务凭据，允许 Agent 写入会扩大敏感配置泄露和误改风险。

#### 修复

- 在 [src/manus_mini/tools/file_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/file_tools.py) 中收紧保护规则。
- 默认保护所有以 `.env` 开头的文件名。
- 保留 `.env.example` 作为模板文件例外，方便项目继续维护安全的配置示例。

#### 回归点

- `write_file` 写 `.env.test` 必须返回 `PROTECTED_PATH`。
- `append_file` 追加 `.env.test` 必须返回 `PROTECTED_PATH`。
- `replace_in_file` 修改 `.env.test` 必须返回 `PROTECTED_PATH`，且原内容保持不变。
- `.env.example` 仍允许作为模板文件写入。

### 36. `list_files` / `read_file` 会暴露敏感配置文件

#### 现象

- 写入路径已经保护 `.env*`，但真实测试发现读路径仍会暴露敏感文件。
- 没有 `.gitignore` 时，`list_files` 会列出 `.env`、`.env.test`、`private.pem`、`service.key`。
- `read_file` 可直接读取 `.env.test` 和 `private.pem` 内容。
- 这和安全文档中 `.env*`、`*.pem`、`*.key` 作为 deny pattern 的目标不一致，也会让模型把密钥内容带入上下文、日志或最终回答。

#### 修复

- 在 [src/manus_mini/tools/file_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/file_tools.py) 中新增统一敏感文件判断。
- `list_files` 默认过滤 `.env*`、`*.pem`、`*.key`。
- `read_file` 直接读取敏感文件时返回 `PROTECTED_PATH`。
- `.env.example` 仍作为安全模板例外，可被列出和读取。

#### 回归点

- `list_files` 无 `.gitignore` 时也不能列出 `.env`、`.env.test`、`*.pem`、`*.key`。
- `read_file` 读取 `.env.test` 必须返回 `PROTECTED_PATH`。
- `read_file` 读取 `*.pem` 必须返回 `PROTECTED_PATH`。
- `.env.example` 仍允许列出和读取。

### 37. `run_bash` / `run_temp_script` 可通过常见读取命令泄露敏感文件

#### 现象

- `list_files` / `read_file` 已经保护敏感文件，但 shell 工具仍可绕过。
- 真实测试中，`cat .env`、`grep secret private.pem`、`head -n 1 .env.test` 会直接把密钥内容输出给模型。
- 这会绕过文件工具的 `PROTECTED_PATH` 保护，让敏感内容进入上下文、日志或最终回答。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中增加本地敏感读取启发式。
- 对 `cat`、`head`、`tail`、`grep`、`sed`、`awk`、`less`、`more` 等常见读取命令，只要目标包含 `.env*`、`*.pem`、`*.key`，就先进入确认流。
- `.env.example` 仍作为安全模板例外。

#### 回归点

- `run_bash` 执行 `cat .env` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- `run_bash` 执行 `grep ... private.pem` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- `run_temp_script` 执行 `head -n 1 .env.test` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- 未确认前，shell 输出不得包含密钥内容。

### 38. `python -c` 读取敏感文件会绕过 shell 敏感读取检测

#### 现象

- 上一轮只覆盖了 `cat`、`grep`、`head` 等常见 shell 读取命令。
- 真实测试中，模型也可通过 `python -c "open('.env').read()"` 或 `Path('.env').read_text()` 读取敏感文件。
- 这类命令不会命中常见读取命令列表，仍会把 `.env` 内容输出给模型。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中扩展敏感读取启发式。
- 新增识别：
  - `open('敏感路径')`
  - `Path('敏感路径').read_text()`
  - `Path('敏感路径').read_bytes()`
  - `Path('敏感路径').open()`
- `.env.example` 仍作为安全模板例外。

#### 回归点

- `run_bash` 执行 `python -c "open('.env').read()"` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- `run_bash` 执行 `Path('.env').read_text()` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- `Path('.env.example').read_text()` 仍允许执行。
- 未确认前，shell 输出不得包含密钥内容。

### 39. 嵌套 shell 可绕过敏感文件读取检测

#### 现象

- 敏感读取检测只分析顶层命令名。
- 真实测试中，`bash -c 'echo nested; cat .env'` 和 `sh -c 'echo nested; head -n 1 .env.test'` 会把敏感文件读取命令藏在 `-c` 脚本文本里。
- 顶层命令名是 `bash` / `sh`，不会命中 `cat` / `head` 等读取命令列表，导致密钥内容直接输出给模型。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中识别 `sh` / `bash` / `zsh` 的 `-c` 脚本文本。
- 使用保留引号语义的 shell token 分段，避免先按分号切分时把 `-c` 脚本文本切坏。
- 对 `-c` 后的脚本文本递归复用敏感读取检测。
- 递归深度限制为两层，避免异常命令造成无限递归或过度分析。

#### 回归点

- `run_bash` 执行 `bash -c 'echo nested; cat .env'` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- `run_temp_script` 执行 `sh -c 'echo nested; head -n 1 .env.test'` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- 未确认前，shell 输出不得包含密钥内容。

### 40. 输入重定向可绕过敏感文件读取检测

#### 现象

- 敏感读取检测主要看命令参数里的敏感路径。
- 真实测试中，`python -c "import sys; print(sys.stdin.read())" < .env` 可通过输入重定向读取敏感文件。
- 嵌套 shell 中同样可以使用 `bash -c 'python ... < .env.test'` 泄露内容。
- 这类命令的顶层程序不一定是 `cat` / `grep` / `head` 等常见读取命令，导致已有规则不会拦截。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中扩展 quote-aware shell token 分析。
- 对 `<` / `<>` 后面的路径执行敏感文件判定。
- 该检测复用嵌套 shell 的递归分析，因此 `bash -c` / `sh -c` 内部输入重定向也会被拦截。

#### 回归点

- `run_bash` 执行 `python -c ... < .env` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- `run_temp_script` 执行 `bash -c 'python -c ... < .env.test'` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- 未确认前，shell 输出不得包含密钥内容。

### 41. 命令替换可绕过敏感文件读取检测

#### 现象

- 敏感读取检测会分析顶层命令和嵌套 shell 脚本文本，但不会进入命令替换片段。
- 真实测试中，`echo $(cat .env)` 的顶层命令是 `echo`，敏感读取藏在 `$()` 内部。
- 临时脚本中也可通过 `bash -c 'echo $(cat .env.test)'` 泄露敏感文件内容。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中提取 `$()` 和反引号命令替换内容。
- 对提取出来的命令替换内容递归复用敏感读取检测。
- 复用既有递归深度限制，避免异常输入造成过度分析。

#### 回归点

- `run_bash` 执行 `echo $(cat .env)` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- `run_temp_script` 执行 `bash -c 'echo $(cat .env.test)'` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- 未确认前，shell 输出不得包含密钥内容。

### 42. `source` / 点命令可加载敏感文件后泄露

#### 现象

- 敏感读取检测覆盖了 `cat`、`grep`、`head` 等读取命令，但没有覆盖 shell 内建的配置加载命令。
- 真实测试中，`set -a; source .env; env` 会先加载 `.env`，再通过 `env` 输出密钥。
- 临时脚本中的 `. .env.test` 也会泄露；同时旧的 quote-aware 分段把换行当普通空白，导致多行脚本里的点命令没有被作为独立命令分析。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中把 `source` 和 `.` 纳入敏感读取命令集合。
- 对 `.` 命令保留原始命令名，避免 `Path('.').name` 归一化为空字符串。
- 在 shell token 分段前把换行作为命令分隔符处理，确保临时脚本多行命令分别分析。

#### 回归点

- `run_bash` 执行 `set -a; source .env; env` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- `run_temp_script` 执行 `. .env.test` 必须返回 `COMMAND_REQUIRES_CONFIRMATION`。
- 未确认前，shell 输出不得包含密钥内容。

### 43. 事件日志和 summary 日志会落盘敏感内容

#### 现象

- TUI 和报告渲染路径已经做了敏感内容脱敏，但 `EventLogger` 落盘前只做压缩，不做脱敏。
- 真实测试中，`logger.record(..., {"result": {"content": "LLM_API_KEY=secret"}})` 会把原始密钥写入 `node.jsonl`。
- `record_summary()` 也会把用户输入和最终结果原样写入 `summary.jsonl`。

#### 修复

- 在 [src/manus_mini/logging.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/logging.py) 中引入统一敏感内容脱敏。
- `record()` 在 `compact_event()` 后对事件 payload 递归执行 `redact_sensitive_value()`。
- `record_summary()` 对 `user_input` 和 `result` 执行 `redact_sensitive_text()` 后再写盘。

#### 回归点

- `node.jsonl` 不得包含 `sk-live-secret`、`abc123`、`secret-token` 等原始敏感值。
- `summary.jsonl` 不得包含用户输入或最终结果里的原始 token/password。
- 脱敏后的日志仍保留字段结构，便于排查问题。

### 44. `/save-context` 导出快照会包含敏感内容

#### 现象

- `/save-context` 会在项目目录生成 `context-*` 快照目录，包含 `session.json` 和 `context.md`。
- 真实测试中，当前会话里包含 `token=secret-token` 或 `password=abc123` 时，两个导出文件都会包含原始敏感值。
- 这类快照更容易被分享或误提交，因此不应默认导出未脱敏内容。

#### 修复

- 在 [src/manus_mini/session.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/session.py) 中为导出快照生成脱敏副本。
- `session.json` 导出前对 `SessionState.model_dump(mode="json")` 递归执行 `redact_sensitive_value()`。
- `context.md` 渲染时对任务目标、消息内容和压缩摘要执行 `redact_sensitive_text()`。
- 内部会话状态仍保留原文，避免破坏恢复会话语义。

#### 回归点

- 导出的 `session.json` 不得包含原始 token/password。
- 导出的 `context.md` 不得包含原始 token/password。
- 导出内容应包含 `[REDACTED]`，让用户知道该处已脱敏。

### 45. Bearer Token 和 URL 查询密钥未脱敏

#### 现象

- 统一脱敏规则只覆盖了 `sk-...`、`api_key=...`、`token=...` 等常见赋值格式。
- 真实测试中，`Authorization: Bearer eyJ...` 会原样保留完整 token。
- URL 里的 `?access_token=secret-token&ok=1` 也会原样保留查询参数密钥。

#### 修复

- 在 [src/manus_mini/redaction.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/redaction.py) 中新增 Bearer 认证头脱敏规则。
- 新增 URL query secret 参数脱敏规则，覆盖 `access_token`、`refresh_token`、`api_key`、`token`、`password`、`secret`。
- 对这类匹配保留原始前缀，只替换敏感值为 `[REDACTED]`，避免破坏日志和 URL 结构。

#### 回归点

- `Authorization: Bearer ...` 不得包含原始 token。
- URL 查询参数中的 secret 值不得包含原文。
- 既有 `api_key=[REDACTED]`、`password=[REDACTED]` 格式保持兼容。

### 46. `session_id` 可通过路径片段越过会话目录

#### 现象

- `SessionStore._path_for()` 直接用 `self.sessions_dir / f"{session_id}.json"` 拼接路径。
- 真实测试中，传入 `../outside` 这类 ID 时，`save/load/delete` 不会拒绝路径片段。
- `delete_logs_for_session("../sessions")` 会把日志目录拼成 `logs/../sessions`，存在误删会话目录的风险。
- CLI 的 `resume/remove` 会把非法 ID 当普通缺失会话处理，不能明确暴露输入非法。

#### 修复

- 在 [src/manus_mini/session_store.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/session_store.py) 中新增 `validate_session_id()`。
- 只允许普通 ID 字符：字母、数字、下划线、点、短横线，且不能包含 `/` 或 `\`。
- `save/load/delete` 和日志清理路径统一复用校验，避免 session 文件和 logs 目录路径穿越。
- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中捕获非法 ID，输出明确错误并退出。

#### 回归点

- `store.save()` 不得接受包含路径片段的 `session_id`。
- `store.load/delete("../outside")` 必须拒绝。
- `delete_logs_for_session("../sessions")` 不得删除 sessions 目录。
- CLI `resume/remove ../...` 必须返回友好错误。

### 47. `fetch_webpage` 可访问本机、内网和云元数据地址

#### 现象

- `fetch_webpage` 只校验 URL 必须以 `http://` 或 `https://` 开头。
- 真实测试中，`http://127.0.0.1:8000/admin`、`http://localhost:8000/admin` 和 `http://169.254.169.254/latest/meta-data/` 会进入真实请求路径。
- 域名如果解析到 `10.0.0.5` 这类私网地址，也会被当成普通网页抓取成功。
- 这会让一个标记为 safe/read-only 的工具具备 SSRF、本机服务探测或云元数据读取风险。

#### 修复

- 在 [src/manus_mini/tools/search_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/search_tools.py) 中，抓取前解析 URL host。
- 对解析出的 IP 地址执行保护判断，拒绝 private、loopback、link-local、multicast、reserved、unspecified 地址。
- 对 DNS 解析失败的 URL 直接拒绝，避免把不可判定目标交给 `requests.get()`。
- 被拒绝时返回 `PROTECTED_URL`，便于上层识别为安全拦截而不是普通网络失败。

#### 回归点

- `fetch_webpage("http://127.0.0.1:...")` 必须返回 `PROTECTED_URL`。
- `fetch_webpage("http://localhost:...")` 必须返回 `PROTECTED_URL`。
- `fetch_webpage("http://169.254.169.254/...")` 必须返回 `PROTECTED_URL`。
- 域名解析到私网地址时，不得调用真实抓取逻辑。
- 正常公网地址仍可抓取并保留 HTML 清洗行为。

### 48. 文件工具越界路径会抛异常而不是返回结构化错误

#### 现象

- `resolve_workspace_path()` 会在路径越过工作区时抛出 `PermissionError("PATH_OUT_OF_WORKSPACE")`。
- 真实测试中，直接调用 `ReadFileTool().run(path="../outside.txt")` 会抛异常，而不是返回 `ToolResult`。
- `write_file`、`replace_in_file`、`append_file`、`make_directory` 的越界路径也存在同类问题。
- 这会让直接工具调用和上层执行器得到不一致的错误形态，降低 Agent 对路径错误的可恢复性和可观测性。

#### 修复

- 在 [src/manus_mini/tools/file_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/file_tools.py) 中新增文件工具路径解析包装。
- 文件工具 `run()` 入口捕获路径越界，并返回 `ToolResult(error_code="PATH_OUT_OF_WORKSPACE")`。
- `resource_keys()` 遇到越界路径时返回空资源键，避免调度资源分析阶段抛异常。
- 保留底层 `resolve_workspace_path()` 的原始语义，避免影响已有调用方。

#### 回归点

- `read_file("../outside.txt")` 不得抛异常，必须返回 `PATH_OUT_OF_WORKSPACE`。
- `write_file`、`replace_in_file`、`append_file`、`make_directory` 的越界路径必须返回结构化错误。
- 正常工作区内文件读写行为保持不变。

### 49. 越界写入的 diff 预览会读取工作区外文件内容

#### 现象

- 写入类工具在真正执行前会生成确认面板 diff 预览。
- 旧实现的 `_build_diff_preview()` 直接用 `(workspace / path).resolve()` 定位目标，再读取旧内容。
- 真实测试中，`write_file(path="../outside-secret.txt")` 虽然执行阶段会被拒绝，但确认面板的 diff 已经包含工作区外文件内容。
- `replace_in_file(path="../outside-secret.txt", confirmed=True)` 也会在执行前写入 trace diff，泄露工作区外内容。

#### 修复

- 在 [src/manus_mini/executor.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/executor.py) 中，diff 预览生成前复用 `resolve_workspace_path()`。
- 如果路径越过工作区，直接不生成 diff 预览。
- 保留工作区内文件的确认 diff 和 replace trace diff 行为。

#### 回归点

- 越界 `write_file` 的 `pending_confirmation.diff_preview` 不得包含工作区外文件内容。
- 越界 `replace_in_file` 的 trace diff 不得包含工作区外文件内容。
- 正常工作区内 `replace_in_file` 和 dry-run 写入仍应显示 diff。

### 50. Shell 命令输出会原样回传敏感值

#### 现象

- `run_bash` 的执行环境已经限制为安全白名单，但命令自身输出仍会直接写入 `ToolResult.content`、`data.stdout` 和 `data.stderr`。
- 如果命令回显 `Authorization: Bearer ...`、URL query 中的 `access_token` 等敏感值，上层模型、日志或报告在拿到工具结果前仍有机会接触原文。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中复用统一的 `redact_sensitive_text`。
- `run_bash` / `run_temp_script` 的正常完成、超时和取消分支，都在写入 `ToolResult` 前先脱敏 stdout/stderr。

#### 回归点

- shell stdout/stderr 中的 Bearer token 不应以原值出现在工具结果中。
- shell stderr 中 URL query secret 参数不应以原值出现在工具结果中。

### 51. 常见环境变量式凭证名未被统一脱敏

#### 现象

- 统一脱敏层此前只覆盖 `api_key`、`token`、`password`、`secret` 这类独立键名。
- `AWS_SECRET_ACCESS_KEY=...`、`CLIENT_SECRET: ...`、`GH_TOKEN=...` 等生产中常见的组合式环境变量名不会被脱敏。
- 由于日志、TUI、报告、会话导出和 shell 输出都复用同一脱敏层，这个缺口会放大到多个输出面。

#### 修复

- 在 [src/manus_mini/redaction.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/redaction.py) 中扩展键名识别。
- 只要键名包含 `API_KEY`、`TOKEN`、`PASSWORD`、`SECRET` 等敏感片段，即可保留键名和分隔符，并将值替换为 `[REDACTED]`。
- 保留 URL query secret 的既有行为，避免新规则吞掉后续非敏感 query 参数。

#### 回归点

- `AWS_SECRET_ACCESS_KEY=...` 必须脱敏值。
- `CLIENT_SECRET: ...` 必须脱敏值。
- `GH_TOKEN=...` 必须脱敏值。
- URL query 中 `access_token` 脱敏后仍保留其他 query 参数。

### 52. 搜索和网页抓取工具会在 URL 输出中泄露 query secret

#### 现象

- `web_search` 会把搜索结果 URL 原样写入工具 `content`。
- `fetch_webpage` 会把原始 URL 写入 `summary` 和 `data.url`。
- 如果 URL 中包含 `access_token`、`api_key` 等 query secret，工具结果在进入上层日志、TUI、报告前已经包含原值。
- 搜索 query 本身也会进入 summary，用户误粘贴 token 时同样会泄漏。

#### 修复

- 在 [src/manus_mini/tools/search_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/search_tools.py) 中复用统一脱敏函数。
- `web_search` 对搜索结果标题、摘要、URL、query、warning 和 stderr 做返回前脱敏。
- `fetch_webpage` 真实请求仍使用原始 URL，但返回的 summary、失败信息和 `data.url` 使用脱敏后的 URL。

#### 回归点

- 搜索结果 URL 中的 `access_token` 不应以原值出现在工具 content。
- 搜索 query 中的 `access_token` 不应以原值出现在工具 summary/data。
- 网页抓取结果中的 URL query secret 不应以原值出现在 summary 或 `data.url`。

### 53. 待确认 diff 和 replace trace diff 会展示敏感值

#### 现象

- 文件写入类工具在执行前会由 executor 生成待确认 diff。
- `replace_in_file` 即使已确认，也会在 trace 中生成非阻塞 diff 预览。
- 这些 diff 不走工具结果脱敏路径，写入普通配置文件时，`CLIENT_SECRET=...` 这类内容会原样出现在 `pending_confirmation.diff_preview` 或 trace event 中。

#### 修复

- 在 [src/manus_mini/executor.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/executor.py) 的 diff 生成边界复用 `redact_sensitive_text`。
- 真实写入/替换仍使用原始内容；只对待确认展示和 trace 预览内容脱敏。

#### 回归点

- `write_file` 的待确认 diff 不应展示原始 `CLIENT_SECRET` 值。
- `replace_in_file` 的 trace diff 不应展示替换前或替换后的原始 secret 值。

### 54. 工具预览 summary 和 args 会暴露敏感值

#### 现象

- `ToolPreview.summary` 和 `ToolPreview.args` 会进入确认流、dry-run 展示和 trace。
- 通用 `BaseTool.preview()` 会把 URL 等参数原样放入 summary/args。
- `run_bash` / `run_temp_script` 有自定义 preview，会把命令或脚本内容中的 `CLIENT_SECRET=...` 等值原样放入 preview。

#### 修复

- 在 [src/manus_mini/tools/base.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/base.py) 中对通用 preview summary 和 args 做统一脱敏。
- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中对自定义 shell preview 的 summary 和 args 做同样脱敏。
- 真实工具执行仍使用原始 tool call 参数，避免脱敏影响实际命令或请求。

#### 回归点

- `fetch_webpage` preview 中 URL query secret 不应以原值出现。
- `run_bash` preview 中命令里的 `CLIENT_SECRET` 不应以原值出现。

### 55. Shell LLM 风险判定会把命令原文发给模型

#### 现象

- `LLMCommandRiskJudge` 会把待执行命令或临时脚本内容打包为 `command_or_script` 发送给 LLM 做风险判断。
- 此路径发生在命令执行前，且不经过工具 result、preview 或日志脱敏。
- 如果命令中包含 `CLIENT_SECRET=...` 等值，外部模型会收到原始 secret。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中，发送给 LLM 风险判定的 `command_or_script` 先执行统一脱敏。
- 本地启发式风险分析仍使用原始命令，避免影响敏感文件读取、写入检测等本地安全判断。

#### 回归点

- LLM 风险判定请求中的命令文本不得包含原始 `CLIENT_SECRET` 值。

### 56. 长期记忆元数据可绕过敏感信息拦截

#### 现象

- `MemoryManager.add_if_allowed()` 只检查 `content` 是否包含敏感信息。
- `tags` 和 `source_message_ids` 会被直接 JSON 序列化写入 sqlite。
- 如果调用方误把 `CLIENT_SECRET=...`、`GH_TOKEN=...` 等值放入 tags 或 source id，长期记忆仍会落盘敏感值。

#### 修复

- 在 [src/manus_mini/memory.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/memory.py) 中让 `add_if_allowed()` 同时检查 content、tags 和 source_message_ids。
- 保留底层 `add()` 的显式写入能力；自动/安全入口继续负责敏感信息拦截。

#### 回归点

- tags 中包含 `CLIENT_SECRET` 时不得写入长期记忆。
- source_message_ids 中包含 `GH_TOKEN` 时不得写入长期记忆。

### 57. 结构化敏感字段值未按字段名脱敏

#### 现象

- `redact_sensitive_value()` 递归处理 dict 时只检查 value 字符串本身。
- 真实日志和工具参数常见结构是 `{"api_key": "plain-secret-value"}` 或 `{"CLIENT_SECRET": "..."}`。
- 这类 value 没有 `api_key=...` 前缀时，原始密钥会绕过统一脱敏层，继续进入日志、确认预览或导出数据。

#### 修复

- 在 [src/manus_mini/redaction.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/redaction.py) 中增加敏感字段名识别。
- 对 `api_key`、`access_token`、`refresh_token`、`password`、`secret` 以及 `_token`、`_secret` 等组合式字段名的值直接替换为 `[REDACTED]`。
- 保留 `token_count` 等非敏感统计字段，避免过度脱敏影响排障。

#### 回归点

- `{"api_key": "plain-secret-value"}` 必须按字段名脱敏。
- 嵌套的 `CLIENT_SECRET` 字段必须按字段名脱敏。
- `token_count` 等统计字段不得被误脱敏。

## 本轮新增/调整测试

- [tests/test_cli.py](/Users/liyong/Desktop/ai-manus/tests/test_cli.py)
  - 顶层 `--cwd` 兼容
  - 旧写法子命令参数兼容
  - `clear` 必须先确认再删除会话
  - `resume` 缺失会话时输出友好错误
  - `resume/remove` 遇到非法 `session_id` 时输出友好错误
- [tests/test_llm.py](/Users/liyong/Desktop/ai-manus/tests/test_llm.py)
  - 原始工具调用 DSL 收口
  - LLM 指数退避和 `Retry-After`
- [tests/test_logging.py](/Users/liyong/Desktop/ai-manus/tests/test_logging.py)
  - 用户目录不可写时路径回退
  - 事件日志落盘前必须递归脱敏敏感字段
  - summary 日志落盘前必须脱敏用户输入和最终结果
- [tests/test_memory.py](/Users/liyong/Desktop/ai-manus/tests/test_memory.py)
  - 长期记忆 content 中的敏感值不得写入
  - 长期记忆 tags 和 source_message_ids 中的敏感值不得写入
- [tests/test_redaction.py](/Users/liyong/Desktop/ai-manus/tests/test_redaction.py)
  - `Authorization: Bearer ...` 必须脱敏 token 值
  - URL query 中的 `access_token` 必须脱敏参数值
  - 常见环境变量式凭证名必须脱敏值
  - 结构化 payload 中敏感字段名对应的值必须脱敏
- [tests/test_session.py](/Users/liyong/Desktop/ai-manus/tests/test_session.py)
  - 待确认状态下普通消息不能绕过确认流
  - `/save-context` 导出的 `session.json` 和 `context.md` 必须脱敏敏感内容
- [tests/test_session_store.py](/Users/liyong/Desktop/ai-manus/tests/test_session_store.py)
  - `session_id` 不得包含路径穿越片段
  - 日志清理不得通过非法 `session_id` 越过 logs 目录
- [tests/test_tools.py](/Users/liyong/Desktop/ai-manus/tests/test_tools.py)
  - 文件工具越界路径必须返回 `PATH_OUT_OF_WORKSPACE`，不得直接抛异常
  - `.env.test` 等环境变量文件变体必须被所有文件写入工具拒绝
  - `.env.example` 仍允许作为模板文件写入
  - `list_files` 默认过滤 `.env*`、`*.pem`、`*.key`
  - `read_file` 直接读取敏感配置或密钥文件时返回 `PROTECTED_PATH`
  - `fetch_webpage` 必须拒绝本机、内网、link-local 和解析到私网的 URL
  - `web_search` / `fetch_webpage` 返回 URL 或 query 前必须脱敏 query secret
  - `fetch_webpage` preview 返回前必须脱敏 URL query secret
- [tests/test_runtime.py](/Users/liyong/Desktop/ai-manus/tests/test_runtime.py)
  - 越界写入/替换不得通过确认 diff 或 trace diff 泄露工作区外文件内容
  - 待确认 diff 和 replace trace diff 不得展示敏感值
  - fallback 高价值回答
  - 空结果保护
  - 行研问答默认不落文件
  - 行研问答不能通过 `run_bash` 绕过默认不落文件策略
  - 行研问答明确“写到文件”时允许进入写入确认流
  - 失败任务结果不会被作为已有产物注入下一轮
  - 搜索 0 结果时增加证据不足提示
  - 搜索失败时增加证据不足提示
  - 搜索有结果但网页抓取全失败时增加页面读取失败提示
  - `replace_in_file` 必须进入确认流
  - `run_bash` 的原地文件修改命令必须进入确认流
  - `run_bash` 的重定向写文件命令必须进入确认流
  - `run_bash` 写生产代码时也必须先通过测试前置门禁
  - `run_bash` 的 `tee` 写文件命令必须进入确认流
  - `run_bash` 的 `tee` 写生产代码命令也必须先通过测试前置门禁
  - `run_bash` 的原地编辑生产代码命令也必须先通过测试前置门禁
  - `run_bash` 的 Python 写生产代码命令也必须先通过测试前置门禁
  - `run_bash` 的 `Path(...).open('w')` 写生产代码命令也必须先通过测试前置门禁
  - `run_bash` 的 `Path(...).write_bytes(...)` 写生产代码命令也必须先通过测试前置门禁
  - `run_bash` 的 `touch` / `Path(...).touch()` 写入必须进入确认流
  - `run_bash` 的 `touch` / `Path(...).touch()` 写生产代码也必须先通过测试前置门禁
  - 复合 shell 命令中后续生产代码写入也必须先通过测试前置门禁
  - 工具重试 trace 记录退避时长
- [tests/test_shell_tools.py](/Users/liyong/Desktop/ai-manus/tests/test_shell_tools.py)
  - shell 超时终止整个子进程组
  - shell 响应 Executor 协作式取消信号
  - shell stdout/stderr 中的敏感值必须先脱敏再返回工具结果
  - shell preview summary/args 中的敏感值必须先脱敏
  - Shell LLM 风险判定请求中的命令文本必须先脱敏
  - `Path(...).write_bytes(...)` 和 `Path(...).open('w')` 写入必须先进入确认流
  - `cat` / `grep` / `head` 等常见 shell 读取命令读取敏感文件时必须先进入确认流
  - `sh` / `bash` / `zsh -c` 嵌套读取敏感文件时必须先进入确认流
  - `< .env*` 输入重定向读取敏感文件时必须先进入确认流
  - `$()` / 反引号命令替换读取敏感文件时必须先进入确认流
  - `source .env*` / `. .env*` 加载敏感文件时必须先进入确认流
  - `python -c` 中 `open()` / `Path.read_text()` 读取敏感文件时必须先进入确认流
- [tests/test_evals.py](/Users/liyong/Desktop/ai-manus/tests/test_evals.py)
  - 声明式 eval 与 runner 一一对应
  - JSON/Markdown 报告生成
  - 未知 runner 配置拒绝
- [tests/test_prompt_tui.py](/Users/liyong/Desktop/ai-manus/tests/test_prompt_tui.py)
  - 英文 reasoning 在 TUI 中直接展示并保留长度截断

## 验证结果

本轮修复完成后，已执行：

```bash
pytest -q
```

结果：

- `422 passed`
- `ruff check src tests evals`：通过
- `mypy`：29 个源码文件无错误
- 分支覆盖率：83.91%（门禁 80%）
- Agent eval：9/9 通过
- `python -m build`：通过，生成 sdist 和 wheel

并额外做了本地脚本级别验证，确认以下场景可正常返回：

- LLM 不可用时回答身份问题
- LLM 不可用时回答启动问题
- LLM 返回空字符串时，最终仍有可展示结果
- 普通行研问答不会误入写文件确认流程
- 普通行研问答不会通过 `run_bash` 绕过默认不落文件策略
- `web_search` 无结果时，最终答案会主动提示“未获取到有效搜索结果”
- `web_search` 执行失败时，最终答案也会主动提示“未获取到有效搜索结果”
- 搜索有结果但网页内容全抓取失败时，最终答案会主动提示“页面内容读取失败”
- `replace_in_file` 不会再直接修改文件，而是先等待确认
- `run_bash` 中明显会改文件的命令会被拦到确认流
- `run_bash` 中重定向写入工作区文件的命令也会被拦到确认流
- `run_bash` 写生产代码时也不能绕过“先测试再改代码”的门禁
- `run_bash` 通过 `tee` 写生产代码时也不能绕过“先测试再改代码”的门禁
- `run_bash` 原地编辑生产代码时也不能绕过“先测试再改代码”的门禁
- `run_bash` 通过 Python 写生产代码时也不能绕过“先测试再改代码”的门禁
- `run_bash` 通过 `Path(...).open('w')` 写生产代码时也不能绕过“先测试再改代码”的门禁
- `run_bash` 通过 `Path(...).write_bytes(...)` 写生产代码时也不能绕过“先测试再改代码”的门禁
- `run_bash` 通过 `touch` / `Path(...).touch()` 写工作区文件时会先等待确认
- `run_bash` 通过 `touch` / `Path(...).touch()` 写生产代码时也不能绕过“先测试再改代码”的门禁
- `run_bash` / `run_temp_script` 通过嵌套 shell 读取敏感文件时会先等待确认
- `run_bash` / `run_temp_script` 通过输入重定向读取敏感文件时会先等待确认
- `run_bash` / `run_temp_script` 通过命令替换读取敏感文件时会先等待确认
- `run_bash` / `run_temp_script` 通过 `source` / `. .env*` 加载敏感文件时会先等待确认
- `run_bash` 通过 `Path(...).write_bytes(...)` 和 `Path(...).open('w')` 写工作区文件时会先等待确认
- `write_file` / `append_file` / `replace_in_file` 不能写入 `.env.test` 等环境变量文件变体
- `.env.example` 仍可作为安全模板文件写入
- `list_files` 不会列出 `.env*`、`*.pem`、`*.key` 等敏感文件
- `read_file` 直接读取敏感配置或密钥文件时会返回 `PROTECTED_PATH`
- `run_bash` / `run_temp_script` 常见读取命令读取敏感文件时会先等待确认
- 复合 shell 命令中后续生产代码写入也不能绕过“先测试再改代码”的门禁
- TUI 会直接展示英文 reasoning，并对过长内容执行截断
- 取消或失败任务的结果不会作为 `已有产物` 污染下一轮上下文
- `EventLogger` 写入事件日志和 summary 日志前会脱敏敏感内容
- `/save-context` 导出的 `session.json` 和 `context.md` 会脱敏敏感内容
- `Authorization: Bearer ...` 和 URL query secret 参数会脱敏敏感值
- `session_id` 包含路径片段时会被拒绝，不会越过 sessions/logs 目录
- `fetch_webpage` 会拒绝本机、内网、link-local 和解析到受保护地址的 URL
- 文件工具越界路径会返回结构化 `PATH_OUT_OF_WORKSPACE` 错误
- 越界写入/替换不会通过确认 diff 或 trace diff 泄露工作区外文件内容
- `run_bash` / `run_temp_script` 返回工具结果前会脱敏 stdout/stderr 中的敏感值
- `AWS_SECRET_ACCESS_KEY`、`CLIENT_SECRET`、`GH_TOKEN` 等组合式凭证名会脱敏值
- `web_search` / `fetch_webpage` 不会在 URL 或 query 输出中泄露 query secret
- 待确认 diff 和 replace trace diff 不会展示 `CLIENT_SECRET` 等敏感值
- 工具 preview summary/args 不会展示 URL query secret 或 shell 命令中的敏感值
- Shell LLM 风险判定请求不会发送命令里的原始敏感值
- 长期记忆安全入口不会通过 tags/source_message_ids 落盘敏感值
- 结构化 payload 中 `api_key` / `CLIENT_SECRET` 字段值会脱敏，`token_count` 不会被误脱敏

## 后续建议

本轮主要修了“启动链路可用性”和“异常情况下的回答收口”。后续还可以继续优化：

1. 项目简介类回答进一步压缩长度，减少 README 复述感。
2. 为规则兜底增加更多高频模板，如“查看历史会话”“恢复会话”“当前项目边界”。
3. 在 TUI 欢迎区增加启动前自检摘要，例如配置来源、存储目录和模型连通性状态。
