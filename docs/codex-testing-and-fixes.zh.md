# Codex 测试与修复记录

本文档记录本轮通过 Codex 对 `manus-mini` 进行真实运行测试后发现并修复的问题，便于后续回归验证、面试演示和继续迭代。

## 测试范围

- CLI 启动链路
- `resume` 交互启动前参数解析
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
- 同时保留 `manus-mini list --cwd .` 这类原有子命令写法。
- 在 [README.md](/Users/liyong/Desktop/ai-manus/README.md) 中将显式启动命令改为当前仍支持的 `list` / `resume` 入口。

#### 回归点

- `manus-mini --cwd .`
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

### 58. 会话列表会暴露完整最近用户消息

#### 现象

- `manus-mini list` 直接打印 `SessionSummary.last_user_message`。
- 如果最近用户消息很长，列表会被整段内容撑开，影响查找会话。
- 如果消息中包含 `token=...` 等敏感值，列表命令会在终端里原样展示。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中为列表展示增加统一格式化。
- 最近用户消息展示前先执行敏感信息脱敏，再折叠换行并截断到固定预览长度。
- 只调整 CLI 展示层，不修改会话持久化数据。

#### 回归点

- `manus-mini list` 不得输出最近用户消息中的原始 token。
- 超长最近用户消息不得完整刷屏，应以省略号截断展示。

### 59. 会话列表遇到损坏文件会整体崩溃

#### 现象

- `SessionStore.list_sessions()` 会对 sessions 目录下所有 `*.json` 直接执行 `_summary()`。
- 只要其中一个会话文件被异常中断、手工编辑或磁盘写入损坏，`manus-mini list` 就会抛 `JSONDecodeError`。
- 结果是一个坏文件会阻断所有正常会话的查看，用户也无法快速定位还可恢复的会话。

#### 修复

- 在 [src/manus_mini/session_store.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/session_store.py) 的列表枚举边界捕获无法读取、解析或迁移的会话文件。
- 跳过损坏文件，继续返回其它正常会话摘要。
- 单个 `resume/load` 的错误语义保持不变，避免掩盖用户明确指定会话时的问题。

#### 回归点

- sessions 目录中存在损坏 JSON 文件时，`manus-mini list` 仍应列出正常会话。
- 损坏文件名不应污染正常列表输出。

### 60. 恢复损坏会话会误报为非法 session id

#### 现象

- `SessionStore.load()` 读取损坏 JSON 时会抛出 `JSONDecodeError`。
- `JSONDecodeError` 是 `ValueError` 子类，`manus-mini resume` 会把它误捕获为非法 `session_id`。
- 用户明确恢复一个存在但损坏的会话时，错误提示会误导排查方向。

#### 修复

- 在 [src/manus_mini/session_store.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/session_store.py) 中增加 `CorruptSessionError`。
- `load()` 在通过 `session_id` 校验并确认文件存在后，将读取、解析、模型迁移失败统一转换为损坏会话错误。
- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中优先捕获损坏会话错误，输出明确的友好错误。

#### 回归点

- `manus-mini resume broken-session` 指向损坏 JSON 时，应提示该会话不可读取或已损坏。
- 非法 `session_id` 与缺失会话的既有友好错误保持不变。

### 61. 事件日志 session_id 可越过日志根目录

#### 现象

- `EventLogger.record()` 和 `record_summary()` 直接使用 `self.root / session_id` 作为写盘目录。
- 如果调用方传入 `../outside` 这类值，日志文件会被写到日志根目录之外。
- 会话存储层已有 `session_id` 校验，但日志写盘入口没有同等边界保护。

#### 修复

- 在 [src/manus_mini/logging.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/logging.py) 中为日志入口增加本地 `session_id` 校验。
- `record()` 和 `record_summary()` 在计算路径和写盘前先拒绝路径穿越、斜杠和非法字符。
- 不从 `session_store` 反向导入校验函数，避免日志模块与会话存储模块形成循环依赖。

#### 回归点

- `EventLogger.record("../outside", ...)` 必须拒绝写盘。
- `EventLogger.record_summary("../outside", ...)` 必须拒绝写盘。
- 日志根目录外不得被创建路径穿越目标。

### 62. 日志清理会把符号链接当真实目录处理

#### 现象

- `SessionStore.clear_all_logs()` 使用 `child.is_dir()` 判断日志目录子项。
- `Path.is_dir()` 会跟随符号链接，指向外部目录的 symlink 会被当作日志目录计数。
- 旧实现不会删除 symlink 本身，导致日志目录中保留可疑入口；后续清理仍会反复遇到同一问题。

#### 修复

- 在 [src/manus_mini/session_store.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/session_store.py) 中提取统一日志入口清理函数。
- 遇到 symlink 时只 `unlink()` 链接本身，不跟随删除外部目标。
- 真实目录继续使用 `shutil.rmtree()` 清理；单个会话日志清理和批量日志清理复用同一安全路径。

#### 回归点

- `clear_all_logs()` 遇到指向外部目录的 symlink 时不得删除外部目录内容。
- symlink 本身应从日志目录移除，避免重复污染后续清理。
- 真实日志目录仍应正常删除。

### 63. 单个会话日志清理会遗留断开的符号链接

#### 现象

- `SessionStore.delete_logs_for_session()` 先用 `Path.exists()` 判断日志入口是否存在。
- 断开的 symlink 在 `exists()` 下返回 false，即使入口本身仍在日志目录中，也会被直接跳过。
- 结果是单个会话清理返回 0 且遗留可疑日志入口，后续排查和清理语义不一致。

#### 修复

- 在 [src/manus_mini/session_store.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/session_store.py) 中调整单个日志入口存在性判断。
- 对“目标不存在但入口本身是 symlink”的情况继续进入 `_remove_log_entry()`，只删除链接本身。
- 非 symlink 的缺失路径仍返回 0，保持原有幂等行为。

#### 回归点

- `delete_logs_for_session()` 遇到断开的 symlink 时应返回 1。
- 断开的 symlink 本身应被删除。
- 缺失的普通日志目录仍应返回 0。

### 64. `python -m manus_mini` 入口不可用

#### 现象

- 实际执行 `python -m manus_mini --help` 时，Python 报 `No module named manus_mini.__main__`。
- 对开发、调试和未安装 console script 的场景不友好。

#### 修复

- 新增 [src/manus_mini/__main__.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/__main__.py)，复用 `manus_mini.cli:main`。
- 保持 `manus-mini` console script 和 `python -m manus_mini` 使用同一 CLI 入口。

#### 回归点

- 包必须暴露 `manus_mini.__main__`。
- `__main__.main` 必须指向现有 CLI `main`。

### 65. 子命令会覆盖命令前的全局 `--cwd`

#### 现象

- 实际使用 `manus-mini --cwd <project> list` 时，子命令 parser 的 `cwd=None` 会覆盖全局 `--cwd`。
- 结果会列出当前目录的会话，而不是用户指定项目的会话。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中将全局 `--cwd` 与子命令 `--cwd` 使用不同 `dest`。
- 主流程按 `subcommand_cwd or global_cwd or Path.cwd()` 解析工作目录。

#### 回归点

- `manus-mini --cwd <project> list` 必须读取 `<project>` 的会话。
- 原有 `manus-mini list --cwd <project>` 仍保持兼容。

### 66. 会话读取会跟随 symlink 读取外部 JSON

#### 现象

- `SessionStore.load()` 只校验 `session_id`，随后直接读取 sessions 目录下的 JSON 文件。
- 如果会话文件是 symlink，运行时会跟随链接读取外部文件，破坏会话存储边界。

#### 修复

- 在 [src/manus_mini/session_store.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/session_store.py) 中拒绝读取 symlink 会话文件。
- `list_sessions()` 复用 summary 解析时也跳过 symlink 会话文件。

#### 回归点

- `load()` 遇到 symlink 会话文件必须报损坏会话错误。
- 会话列表不得因为 symlink 指向外部 JSON 而展示外部内容。

### 67. 会话保存会覆盖 symlink 指向的外部文件

#### 现象

- `SessionStore.save()` 直接对目标路径 `write_text()`。
- 如果 sessions 目录中已有同名 symlink，会写入链接目标，可能覆盖工作区外文件。

#### 修复

- 保存前检查目标会话路径是否为 symlink。
- 命中 symlink 时拒绝保存并报告损坏会话路径，不写入外部目标。

#### 回归点

- 保存同名 symlink 会话文件时必须失败。
- symlink 指向的外部文件内容不得被修改。

### 68. 删除断开的会话 symlink 会误报不存在并遗留入口

#### 现象

- `SessionStore.delete()` 先用 `Path.exists()` 判断会话文件。
- 断开的 symlink 在 `exists()` 下返回 false，导致 `remove` 误报不存在并留下 sessions 目录污染。

#### 修复

- 删除会话时先识别 symlink。
- symlink 会话入口只删除链接本身，返回已删除；普通缺失路径仍返回未找到。

#### 回归点

- 删除断开的会话 symlink 必须返回成功。
- 链接本身必须从 sessions 目录移除。

### 69. 旧项目存储迁移会复制 symlink 指向的外部会话文件

#### 现象

- legacy `.manus-mini/sessions/*.json` 迁移使用 `is_file()` 判断源文件。
- `Path.is_file()` 会跟随 symlink，导致迁移把外部 JSON 内容复制进当前项目会话存储。

#### 修复

- 在 [src/manus_mini/logging.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/logging.py) 的 legacy 迁移中显式跳过 symlink。
- 只迁移真实 legacy 会话 JSON 文件。

#### 回归点

- legacy sessions 目录中的 symlink JSON 不得被迁移。
- 真实 legacy 会话文件仍应正常迁移。

### 70. 事件日志写入会跟随 symlink 日志目录

#### 现象

- `EventLogger.record()` 和 `record_summary()` 会对 `logs/<session_id>` 执行 `mkdir()` 后写入日志文件。
- 如果该 session 日志目录是 symlink，会把事件日志写到外部目录。

#### 修复

- 在 [src/manus_mini/logging.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/logging.py) 中增加日志目录 symlink 检查。
- 写入 node/pipeline/summary 前先拒绝 symlink 日志目录。

#### 回归点

- symlink 日志目录下不得写入 `node.jsonl`。
- symlink 日志目录下不得写入 `summary.jsonl`。

### 71. `run_bash` 可无确认复制敏感文件

#### 现象

- 实际使用 `run_bash` 执行 `cp .env leaked.txt` 会直接复制敏感文件。
- 旧的敏感读取检测覆盖了 `cat`、`grep`、`source` 等读命令，但没有覆盖复制命令。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中识别 `cp` 命令读取 `.env*`、`*.pem`、`*.key`。
- 命中后进入人工确认流，默认不执行复制。

#### 回归点

- `run_bash` 执行 `cp .env leaked.txt` 必须要求确认。
- 未确认时不得生成复制后的目标文件。

### 72. `run_temp_script` 可无确认复制敏感文件

#### 现象

- `run_temp_script` 复用 shell 风险分析，但旧规则同样漏掉 `cp .env.test leaked.txt`。
- 这会让临时脚本绕过敏感文件读取确认。

#### 修复

- 临时脚本内容同样走增强后的 `cp` 敏感路径检测。
- 未确认时阻止脚本执行。

#### 回归点

- `run_temp_script` 中复制 `.env.test` 必须要求确认。
- 未确认时不得生成复制后的目标文件。

### 73. FD 前缀重定向写文件会绕过确认流

#### 现象

- 旧重定向规则只匹配 `> file` 和 `>> file`。
- `1>out.txt`、`2>err.log` 这类常见 FD 重定向会直接创建或覆盖工作区文件。

#### 修复

- 扩展 shell 写入重定向检测，支持可选数字 FD 前缀。
- 保留 `2>&1` 等 FD 合并场景的排除逻辑。

#### 回归点

- `1>out.txt` 写工作区文件必须要求确认。
- `2>err.log` 写工作区文件必须要求确认。

### 74. 写文件工具对目录目标抛原始异常

#### 现象

- `write_file` 指向已有目录时，会从 `Path.write_bytes()` 抛 `IsADirectoryError`。
- `append_file` 指向已有目录时，也可能在读取或追加时抛原始异常。
- 这和其它文件工具返回结构化错误的体验不一致。

#### 修复

- 在 [src/manus_mini/tools/file_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/file_tools.py) 中为 `write_file` 和 `append_file` 增加目录目标检查。
- 已存在但不是普通文件时返回 `INVALID_TOOL_PARAMS` 和 `not a file` 摘要。

#### 回归点

- `write_file` 指向目录时必须返回结构化错误。
- `append_file` 指向目录时必须返回结构化错误。

### 75. `resume` 静默忽略本次传入的 dry-run 和 limits

#### 现象

- 实际使用 `manus-mini --dry-run --max-react 1 resume <session>` 时，CLI 已解析参数，但 `_run_resume()` 没有接收这些参数。
- 恢复会话后仍使用默认或旧限制，用户以为开启了 dry-run/限制回合，实际没有生效。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中将 `dry_run` 和 `max-*` 参数传入 resume 启动路径。
- `PromptTuiOptions` 使用本次 CLI 参数构造，保证用户显式输入生效。

#### 回归点

- `manus-mini --dry-run --max-react 1 resume <session>` 必须开启 dry-run。
- 恢复后的运行限制必须使用本次传入的 `max-react=1`。

### 76. TUI 待确认时输入“取消”会被 Enter 当作确认

#### 现象

- 待确认写入出现时，TUI 的 Enter 绑定直接调用确认函数，不读取输入框内容。
- 用户按提示输入“取消”再回车时，仍会触发确认流程，属于 UX 和安全风险。

#### 修复

- 在 [src/manus_mini/prompt_tui.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/prompt_tui.py) 中增加确认输入提交路径。
- 输入框非空时走 `SessionManager.handle_user_message()` 的确认/取消语义；空 Enter 保留快捷确认。

#### 回归点

- 待确认时输入 `取消` 再提交必须拒绝 pending 操作。
- 拒绝时不得启动确认后的后台执行。

### 77. TUI 确认输入后未清空输入框

#### 现象

- 待确认状态下输入确认/取消文本后，如果不清空输入框，后续焦点仍在旧内容上。
- 这会造成用户继续输入时混入上一条确认文本。

#### 修复

- `submit_confirmation_input()` 在处理非空确认文本前清空输入框。
- 空 Enter 快捷确认不改动输入框内容。

#### 回归点

- 待确认时输入 `取消` 后，输入框内容必须被清空。

### 78. `resume` 未传 limits 时会覆盖已保存任务限制

#### 现象

- 修复 resume 支持本次 CLI limits 后，如果无条件使用默认值，会把已保存 active task 的运行限制覆盖掉。
- 用户只是恢复会话时，历史任务上下文里的限制不应被默认值重置。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中识别用户本次是否显式传入 `--max-*`。
- 只有显式传入的限制才覆盖已保存 active task limits；未传入时保留保存值。

#### 回归点

- `resume` 不带 `--max-react` 时，应保留已保存 active task 的 `max_react_iterations`。
- `resume --max-react 1` 仍应使用本次传入值。

### 79. `run_bash` 可无确认移动敏感文件

#### 现象

- 上轮已拦截 `cp .env leaked.txt`，但实际使用中 `mv .env leaked.txt` 仍会直接执行。
- 这会把敏感配置文件移出原位置，并生成新的未保护文件名。

#### 修复

- 在 [src/manus_mini/tools/shell_tools.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/tools/shell_tools.py) 中把 `mv` 纳入敏感文件外带命令集合。
- 命中 `.env*`、`*.pem`、`*.key` 时先进入确认流。

#### 回归点

- `mv .env leaked.txt` 未确认时不得执行。
- 原敏感文件必须保留，目标文件不得生成。

### 80. `install` 命令可无确认复制密钥文件

#### 现象

- `install private.pem copied.pem` 会读取源文件并写出副本，但旧规则没有识别。
- 在开发环境里 `install` 是常见复制/安装命令，可能被用来外带密钥。

#### 修复

- 将 `install` 纳入敏感文件外带命令集合。
- 读取敏感路径时要求人工确认。

#### 回归点

- `install private.pem copied.pem` 未确认时不得执行。
- 目标密钥副本不得生成。

### 81. `tar` 可无确认打包敏感文件

#### 现象

- `tar -cf leaked.tar .env` 不直接打印敏感内容，但会生成包含敏感文件的归档。
- 旧敏感读取规则没有覆盖归档命令。

#### 修复

- 将 `tar` 纳入敏感文件外带命令集合。
- 命令参数中出现敏感路径时进入确认流。

#### 回归点

- `tar -cf leaked.tar .env` 未确认时不得执行。
- 敏感归档文件不得生成。

### 82. `rsync` 可无确认复制敏感文件

#### 现象

- `rsync .env leaked.txt` 是复制敏感文件的另一条常见路径。
- 如果系统安装了 `rsync`，旧实现会直接执行。

#### 修复

- 将 `rsync` 纳入敏感文件外带命令集合。
- 风险检测在命令执行前完成，不依赖系统是否安装该命令。

#### 回归点

- `rsync .env leaked.txt` 未确认时必须被拦截。
- 目标文件不得生成。

### 83. 强制压缩可能记录“移除 0 条”的假快照

#### 现象

- `force_truncate_history()` 会强保留 system segment 和首尾用户消息。
- 当所有可保留段都不能移除时，压缩结果没有变化，但仍生成 `force_truncate` snapshot，摘要显示移除 0 条。
- 用户会看到已压缩状态，但上下文 token 实际没有下降。

#### 修复

- 在 [src/manus_mini/context.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/context.py) 中检测强制截断是否实际覆盖了消息。
- 如果消息未减少且内容未改写，返回 `snapshot=None`，避免记录假压缩。

#### 回归点

- 强制截断没有移除任何消息时，不得生成 compression snapshot。
- 原消息列表应保持不变。

### 84. TUI 异常失败不保存到会话，影响恢复排障

#### 现象

- `PromptTui.run_agent_turn()` 捕获普通异常后只更新界面。
- 会话里的 active task 没有标记失败，错误信息也没有写入 session store。
- 用户重新 `resume` 或 `list` 时看不到这次失败记录。

#### 修复

- 在 [src/manus_mini/prompt_tui.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/prompt_tui.py) 的异常渲染路径中同步更新 session。
- active task 标记为 failed，记录 `UNKNOWN_ERROR`，写入系统消息，并调用 `_save_current()`。

#### 回归点

- TUI 普通异常后 active task 必须变为 failed。
- 错误信息必须写入消息和 task result。
- 异常状态必须被保存，供后续 resume 排查。

### 85. `manus-mini list` 有会话时输出为裸 TSV，演示和排障可读性差

#### 现象

- 亲自执行 `python -m manus_mini list --cwd /Users/liyong/Desktop/ai-manus` 时，输出只有无表头 TSV 行。
- 用户无法直接看出每列含义、会话总数、会话存储目录，也没有下一步如何恢复会话的提示。
- 这类问题不影响核心执行，但会明显拉低面试/demo 时的工程成熟度和可诊断性。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中优化 `list` 输出。
- 有会话时固定展示：
  - `Session directory`
  - `Saved sessions` 总数
  - `SESSION ID / UPDATED / MESSAGES / LAST USER MESSAGE` 表头
  - 默认恢复最新会话的 `manus-mini resume ... --cwd ...` 示例命令
- 保留最近用户消息的脱敏和截断逻辑，避免为了可读性回退安全边界。

#### 回归点

- `manus-mini list` 有会话时必须展示目录、总数、表头和恢复命令。
- 最近用户消息仍必须先脱敏并截断。
- 空会话时仍保持简洁提示，不输出无意义表头。

### 86. `manus-mini clear` 在非交互环境会抛 `EOFError` 栈

#### 现象

- 亲自执行 `python -m manus_mini clear --cwd /Users/liyong/Desktop/ai-manus` 时，stdin 没有可读输入会触发 `EOFError`。
- 旧实现直接泄露 Python traceback，用户无法判断会话是否已被删除。
- 这属于 CLI 删除类操作的可诊断性和安全感问题。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中捕获确认输入阶段的 `EOFError`。
- stdin 不可读时按取消处理，输出 `Clear cancelled.`，不删除任何会话。

#### 回归点

- 非交互执行 `clear` 不得输出 traceback。
- stdin 缺失时必须保留原会话。

### 87. `resume` 子命令不接受 session id 后面的运行参数

#### 现象

- 亲自执行 `python -m manus_mini resume <session-id> --max-react 1 --cwd ...` 时，旧实现报 `unrecognized arguments`。
- 但交互入口支持后置运行参数，且用户自然会把恢复会话的参数写在 session id 后面。
- 这会让面试演示中的“恢复会话并覆盖本轮限制”显得不一致。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中给 `resume` 子命令注册交互运行参数。
- 支持 `--dry-run`、`--max-steps`、`--max-react`、`--max-reflect`、`--max-tool-retries` 在 `resume <session-id>` 后传入。

#### 回归点

- `manus-mini resume <session-id> --max-react 1 --cwd ...` 必须能通过解析。
- 后置 runtime 参数必须真正覆盖本次恢复运行的 limits。

### 88. 非终端环境启动交互界面会泄露 prompt_toolkit 栈

#### 现象

- 在非终端环境进入交互界面时，旧实现会抛出 prompt_toolkit / asyncio traceback。
- 这类框架栈对用户没有帮助，也会明显影响命令行工具的专业度。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中进入 TUI 前先检查 stdin 是否是终端。
- 非终端环境直接输出友好错误：
  - `Error: interactive TUI requires a terminal. Use 'manus-mini --help' for non-interactive commands.`
- 同时保留对 TUI 运行阶段终端错误的兜底捕获。

#### 回归点

- 非终端执行 `resume` 不得输出框架 traceback。
- 提示信息必须明确说明交互 terminal UI 需要 terminal。

### 89. 旧交互子命令不再使用但仍暴露在 CLI 和文档中

#### 现象

- 用户明确说明旧交互子命令不会再使用。
- 旧 CLI 仍注册该子命令，无参运行也会尝试进入交互界面。
- README 和规则兜底回答仍会推荐旧启动命令，容易让面试演示路径和实际使用方式不一致。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中删除直接启动交互界面的子命令和默认无参启动路径。
- 无参执行现在只打印帮助，不再启动交互界面。
- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中将启动兜底回答改为 `list` / `resume`。
- 在 [README.md](/Users/liyong/Desktop/ai-manus/README.md) 中删除旧启动命令示例。

#### 回归点

- 旧子命令必须被 argparse 拒绝。
- 无参执行必须只展示帮助，不得启动 `PromptTui`。
- 启动说明和 README 不得继续推荐旧子命令。

### 92. 清理旧交互子命令的残留指令与代码

#### 现象

- 直接入口删除后，规划器、反思器、包导入测试和部分文档仍把旧交互子命令当成有效概念。
- `prompt_tui.py` 还保留了只用于直连入口的 `main()` 包装函数。
- `summary.md` 仍记录早期 Textual 启动方式，和当前 `run/list/resume` 使用路径不一致。

#### 修复

- 在 [src/manus_mini/planner.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/planner.py) 和 [src/manus_mini/reflector.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/reflector.py) 中删除旧交互子命令关键词。
- 在 [src/manus_mini/prompt_tui.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/prompt_tui.py) 中删除直连 `main()` 包装函数。
- 将交互帮助、技术设计、生产化说明和项目摘要改为 `run/list/resume` 语义，不再推荐旧启动方式。

#### 回归点

- CLI help 不得出现旧交互子命令或 terminal UI 入口说明。
- 规划器不能因为用户提到旧交互词就误判成 CLI 用法问题。
- 反思器不能把旧交互词本身当成完整 CLI 用法说明。

### 90. 删除直接交互入口后，新用户没有创建第一条会话的命令

#### 现象

- 亲自执行 `python -m manus_mini --help` 和空项目 `list` 后发现，只剩 `list/resume/remove/clear`。
- 对一个没有历史会话的新项目来说，用户无法从 CLI 创建第一条会话。
- 这会让面试演示的首条路径断掉：工具看起来只能恢复历史，不能开始任务。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中新增 `run` 子命令。
- `run` 会创建新 session，执行一次用户问题，保存 session，并输出：
  - 最终回答
  - `Session ID`
  - 任务状态
  - 后续 `resume` 命令
- 空会话 `list` 会提示 `manus-mini run "你的问题" --cwd ...`，帮助新用户闭合首条使用路径。
- README 和规则兜底启动说明同步改为先用 `run`。

#### 回归点

- `manus-mini run "问题" --cwd ...` 必须创建并保存 session。
- `list` 空会话时必须提示如何创建第一条会话。
- 启动说明必须包含 `run` 入口。

### 91. 项目级用户存储父目录可写但项目目录不可写时不会回退

#### 现象

- 亲自执行 `python -m manus_mini run "怎么启动和使用？" --cwd /private/tmp/manus-mini-run-check` 时，日志目录创建阶段抛出 `PermissionError`。
- 根因是 `project_storage_dir()` 只验证了 `~/.manus-mini/projects` 父目录可创建，没有验证具体项目目录可创建。
- 当父目录存在但当前项目目录创建失败时，运行时仍继续使用不可写路径。

#### 修复

- 在 [src/manus_mini/logging.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/logging.py) 中让 `project_storage_dir()` 同时创建并验证具体项目存储目录。
- 只要用户级项目目录创建失败，就回退到工作区 `.manus-mini`。

#### 回归点

- 用户级项目目录不可写时，必须回退到工作区 `.manus-mini`。
- `manus-mini run` 在该场景下不得抛原始 `PermissionError`。

### 93. 首次运行提示和中文分词兜底不够适合面试演示

#### 现象

- 亲自执行 `python -m manus_mini --help` 时，只能看到参数列表，没有首条示例和恢复路径。
- 执行 `python -m manus_mini run --help` 时，`prompt` 位置参数没有说明，也没有提示多词 prompt 要加引号。
- 执行 `python -m manus_mini run --cwd <目录>` 时，argparse 只报缺少 `prompt`，没有下一步示例。
- 执行 `python -m manus_mini run "" --cwd <目录>` 时，只输出 `Error: prompt is required.`，没有可复制命令。
- 执行 `python -m manus_mini run 总结 当前 项目 --cwd <目录> --max-steps 1 --max-react 1` 时，中文被 shell 分词成 `总结 当前 项目`，规则兜底无法识别“当前项目”，导致输出低价值的网络错误兜底。

#### 修复

- 在 [src/manus_mini/cli.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/cli.py) 中为顶层 help 和 `run --help` 增加首条运行示例、引号说明和恢复命令。
- `run` 缺少 `prompt` 时，在 argparse 错误后追加可复制示例。
- 空 prompt、空会话列表、缺失会话恢复错误都补充下一步命令。
- 在 [src/manus_mini/react.py](/Users/liyong/Desktop/ai-manus/src/manus_mini/react.py) 中压缩中文字符之间的空格后再识别“当前项目”，兼容未加引号的中文 prompt。

#### 回归点

- `manus-mini --help` 必须展示项目分析示例和 resume 示例。
- `manus-mini run --help` 必须说明 `prompt`，并提示多词 prompt 需要加引号。
- 缺少 prompt、空 prompt、空会话列表、缺失 session 都必须给出可执行下一步。
- `总结 当前 项目` 必须命中当前项目概览兜底，不能退回只展示网络错误原因。

## 本轮新增/调整测试

- [tests/test_context.py](/Users/liyong/Desktop/ai-manus/tests/test_context.py)
  - 强制截断没有实际减少上下文时不生成假 compression snapshot
- [tests/test_cli.py](/Users/liyong/Desktop/ai-manus/tests/test_cli.py)
  - `python -m manus_mini` 复用 CLI 入口
  - 顶层 `--cwd` 兼容
  - 子命令保留命令前的全局 `--cwd`
  - 旧写法子命令参数兼容
  - `clear` 必须先确认再删除会话
  - `resume` 缺失会话时输出友好错误
  - `resume` 使用本次传入的 dry-run 和 limits
  - `resume` 支持 session id 后传入 dry-run 和 limits
  - `resume` 未传 limits 时保留已保存 active task limits
  - `resume` 在非终端环境输出友好错误，而不是泄露 prompt_toolkit 栈
  - `resume` 指向损坏会话文件时输出友好错误
  - `resume/remove` 遇到非法 `session_id` 时输出友好错误
  - `list` 有会话时展示会话目录、总数、表头和恢复命令
  - `list` 展示最近用户消息前必须脱敏并截断预览
  - `list` 遇到损坏会话文件时仍能列出正常会话
  - 旧交互启动子命令被拒绝
  - 无参运行只打印帮助，不再打开交互界面
  - `run` 创建新会话、输出结果和恢复命令
  - 空会话 `list` 提示使用 `run` 创建第一条会话
  - 顶层 help 展示项目分析示例和 resume 示例
  - `run --help` 展示 prompt 说明、引号提示和项目分析示例
  - `run` 缺少 prompt 时输出可复制示例
  - `run ""` 空 prompt 时输出可复制示例
  - 缺失 session 的 `resume` 错误会提示先执行 `list`
  - `clear` stdin 缺失时按取消处理，不删除会话
  - `--help` 展示项目说明、参数用途和默认值，便于首次运行诊断
  - `list/clear --help` 展示 `--cwd` 和 `--force` 等子命令参数用途
- [tests/test_llm.py](/Users/liyong/Desktop/ai-manus/tests/test_llm.py)
  - 原始工具调用 DSL 收口
  - LLM 指数退避和 `Retry-After`
- [tests/test_logging.py](/Users/liyong/Desktop/ai-manus/tests/test_logging.py)
  - 用户目录不可写时路径回退
  - 用户级项目目录不可写时回退到工作区 `.manus-mini`
  - 事件日志落盘前必须递归脱敏敏感字段
  - summary 日志落盘前必须脱敏用户输入和最终结果
  - 事件日志和 summary 日志必须拒绝非法 `session_id` 路径穿越
  - 事件日志和 summary 日志不得写入 symlink 日志目录
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
  - 读取会话时拒绝 symlink 会话文件
  - 保存会话时拒绝覆盖 symlink 指向的外部文件
  - 删除会话时会移除断开的 symlink 会话入口
  - 日志清理不得通过非法 `session_id` 越过 logs 目录
  - 批量日志清理不得跟随 symlink 删除外部目录
  - 单个会话日志清理会删除断开的 symlink 本身
  - legacy 会话迁移会跳过 symlink JSON 文件
- [tests/test_tools.py](/Users/liyong/Desktop/ai-manus/tests/test_tools.py)
  - 文件工具越界路径必须返回 `PATH_OUT_OF_WORKSPACE`，不得直接抛异常
  - 写文件和追加文件指向目录时返回结构化错误
  - `.env.test` 等环境变量文件变体必须被所有文件写入工具拒绝
  - `.env.example` 仍允许作为模板文件写入
  - `list_files` 默认过滤 `.env*`、`*.pem`、`*.key`
  - `list_files` 遇到指向工作区外的 symlink 文件或目录时跳过，不展示外部路径也不崩溃
  - `read_file` 直接读取敏感配置或密钥文件时返回 `PROTECTED_PATH`
  - `make_directory` 拒绝创建隐藏目录，避免绕过隐藏路径写入保护
  - `fetch_webpage` 必须拒绝本机、内网、link-local 和解析到私网的 URL
  - `fetch_webpage` 必须拒绝重定向到本机、内网、link-local 和解析到私网的 URL
  - `fetch_webpage` 对大小写 HTTP/HTTPS scheme 兼容处理
  - `fetch_webpage` 非整数 `max_chars` 和非法端口必须返回结构化 `INVALID_TOOL_PARAMS`
  - `fetch_webpage` 正确解码数字 HTML entity 和常见命名 entity
  - `web_search` 非整数 `max_results` 必须返回结构化 `INVALID_TOOL_PARAMS`
  - `web_search` / `fetch_webpage` 返回 URL 或 query 前必须脱敏 query secret
  - `fetch_webpage` preview 返回前必须脱敏 URL query secret
- [tests/test_runtime.py](/Users/liyong/Desktop/ai-manus/tests/test_runtime.py)
  - 越界写入/替换不得通过确认 diff 或 trace diff 泄露工作区外文件内容
  - 待确认 diff 和 replace trace diff 不得展示敏感值
  - fallback 高价值回答
  - `总结 当前 项目` 这类中文分词后的 prompt 仍能命中当前项目概览兜底
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
  - `cp .env*` 复制敏感文件必须先进入确认流
  - `mv` / `install` / `tar` / `rsync` 外带敏感文件必须先进入确认流
  - `zip` / `gzip` 压缩敏感文件必须先进入确认流
  - `base64` / `wc` / `openssl -in` 读取敏感文件必须先进入确认流
  - `python -c` 中通过变量间接 `open()` / `Path.read_text()` 读取敏感文件必须先进入确认流
  - `python -c` 中通过 `shutil.copyfile()` 复制敏感文件必须先进入确认流
  - `1>out.txt` / `2>err.log` 这类 FD 重定向写工作区文件必须先进入确认流
- [tests/test_session_store.py](/Users/liyong/Desktop/ai-manus/tests/test_session_store.py)
  - `list_sessions` 遇到 symlink 会话文件时跳过，不能让列表命令崩溃
  - 会话文件内容中的 `session_id` 必须与文件名一致，避免伪造会话污染列表
  - `resume` 加载会话时必须拒绝 payload `session_id` 与请求 ID 不一致的文件
- [tests/test_cli.py](/Users/liyong/Desktop/ai-manus/tests/test_cli.py)
  - `--max-steps` / `--max-react` / `--max-reflect` / `--max-tool-retries` 必须拒绝 0 或负数，避免 CLI 进入不可预期循环配置
- [tests/test_evals.py](/Users/liyong/Desktop/ai-manus/tests/test_evals.py)
  - 声明式 eval 与 runner 一一对应
  - JSON/Markdown 报告生成
  - 未知 runner 配置拒绝
- [tests/test_prompt_tui.py](/Users/liyong/Desktop/ai-manus/tests/test_prompt_tui.py)
  - 英文 reasoning 在 TUI 中直接展示并保留长度截断
  - 待确认时输入 `取消` 再提交会拒绝 pending 操作，而不是误确认
  - 普通异常失败会写入会话并保存，便于后续恢复排障

## 验证结果

本轮修复完成后，已执行：

```bash
pytest -q
```

结果：

- `481 passed`
- `ruff check src tests evals`：通过
- `mypy`：30 个源码文件无错误
- 分支覆盖率：84.27%（门禁 80%）
- Agent eval：9/9 通过
- `python -m build`：通过，生成 sdist 和 wheel
- `python -m manus_mini --help`：通过，能正常展示 CLI 帮助

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
- `manus-mini list` 展示最近用户消息前会脱敏并截断长文本
- `manus-mini list` 遇到损坏会话文件时仍能列出其它正常会话
- `manus-mini resume` 指向损坏会话文件时会输出明确的友好错误
- `EventLogger` 写入事件日志和 summary 日志前会拒绝非法 `session_id` 路径穿越
- 批量日志清理会删除 symlink 本身但不会跟随删除外部目录
- `manus-mini clear` 在 stdin 不可读时会取消操作，不再输出 traceback
- `manus-mini resume <session-id> --max-react 1 --cwd ...` 不再被 argparse 拒绝
- `manus-mini resume` 在非终端环境输出友好错误，不再泄露 prompt_toolkit 栈
- 旧交互启动子命令已被拒绝，无参执行只打印帮助
- `python -m manus_mini --help` 不再展示旧交互启动子命令
- 规则兜底的启动说明不再推荐旧交互启动子命令
- `manus-mini run "怎么启动和使用？" --cwd /private/tmp/manus-mini-run-check` 可以创建并保存新会话
- `manus-mini list --cwd /private/tmp/manus-mini-run-check` 能展示刚创建的会话

## 后续建议

本轮主要修了“启动链路可用性”和“异常情况下的回答收口”。后续还可以继续优化：

1. 项目简介类回答进一步压缩长度，减少 README 复述感。
2. 为规则兜底增加更多高频模板，如“查看历史会话”“恢复会话”“当前项目边界”。
3. 在 `resume` 交互界面欢迎区增加启动前自检摘要，例如配置来源、存储目录和模型连通性状态。
