# Vora 安全与权限模型

## 安全目标

Agent 可以读取文件、修改文件、执行命令，因此安全边界必须由执行层保证，不能只依赖模型“自觉”。当前项目采用分层防护：

1. 工具 schema 限制输入形状。
2. 文件工具限制 workspace 路径。
3. 写入工具必须 preview + 确认。
4. 命令工具做禁用模式、风险判断、确认和超时。
5. memory 和日志输出做敏感信息过滤。

## 当前权限分级

| 级别 | 示例工具 | 当前策略 |
|---|---|---|
| `safe` | `list_files`、`read_file` | 默认可执行，但限制 workspace，并过滤噪声目录。 |
| `write` | `write_file`、`replace_in_file`、`append_file`、`make_directory` | 必须生成 preview，用户确认后才执行。 |
| `command` | `run_bash`、`run_temp_script` | 禁止明显危险命令，高风险命令需要确认，支持 timeout 和输出截断。 |

## 文件访问边界

当前文件工具通过 `resolve_workspace_path()` 约束路径：

- 相对路径会解析到当前 workspace。
- workspace 外路径默认拒绝。
- 系统临时目录作为受控例外，用于临时测试和工具执行。
- `.gitignore`、依赖目录、构建产物等噪声目录在列表工具中默认过滤。

## 写入确认

写入类工具必须满足：

1. 先生成变更摘要。
2. 对文本变更生成 diff preview。
3. 用户确认后才执行真实写入。
4. 用户拒绝时返回取消结果，不把拒绝视为系统错误。

这保证 Agent 不会因为模型一次错误 tool call 直接修改项目文件。

## 命令执行边界

当前命令工具做了三层限制：

- 静态拒绝：例如 `sudo`、`rm -rf /`、`mkfs`、`shutdown` 等危险模式。
- 风险判断：可接 LLM risk judge，对高风险命令要求确认。
- 执行限制：subprocess 使用受控 env、工作目录、timeout、输出截断。

当前不足：

- 仍然是本机 subprocess，不是强沙箱。
- 不能完全阻止复杂 shell 绕过。
- 网络访问还没有统一权限模型。

## 敏感信息处理

当前策略：

- memory 写入前过滤 `API_KEY`、`TOKEN`、`PASSWORD`、`SECRET` 等明显敏感内容。
- TUI 和日志展示路径中有 redaction 辅助逻辑。
- `.env` 等敏感文件不应主动纳入长期记忆。

后续应扩展为统一 redaction pipeline，覆盖：

- LLM request / response。
- tool result。
- trace event。
- artifact。
- memory。

## 生产化权限模型

建议演进为可配置策略：

```text
policy:
  filesystem:
    read_roots: ["./"]
    write_roots: ["./src", "./docs", "./tests"]
    deny_patterns: [".env*", "*.pem", "*.key"]
  commands:
    allow: ["pytest", "ruff", "python -m pytest"]
    deny: ["sudo", "rm -rf", "curl | sh"]
    require_confirmation: true
  network:
    default: "deny"
    allow_domains: []
```

## 面试表达重点

可以重点讲：

- 安全策略在工具执行层二次校验，不信任模型输出。
- 写入和命令是 human-in-the-loop，不是静默执行。
- 当前是本地 MVP，强沙箱和多租户权限是生产化演进项。
