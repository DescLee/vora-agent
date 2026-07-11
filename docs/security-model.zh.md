# Vora 安全与权限模型

## 安全目标

Agent 可以读取文件、修改文件、执行命令，因此安全边界必须由执行层保证，不能只依赖模型“自觉”。当前项目采用分层防护：

1. 工具 schema 限制输入形状。
2. 文件工具限制 workspace 路径。
3. `read_file`、`write_file`、`replace_in_file` 按用户明确要求直接执行，不再等待人工确认。
4. 写入工具保留 diff preview、workspace 边界和 dry-run 不落盘。
5. 命令工具做禁用模式、风险判断、确认和超时。
6. memory 和日志输出做敏感信息过滤。

## 当前权限分级

| 级别 | 示例工具 | 当前策略 |
|---|---|---|
| `safe` | `list_files`、`read_file` | 默认可执行，但限制 workspace，并过滤噪声目录。 |
| `write` | `write_file`、`replace_in_file` | 按用户要求直接执行；执行前记录 diff preview，仍受 workspace/protected path、精确替换和 large rewrite guard 约束。 |
| `write-confirmed` | `append_file`、`make_directory` | 仍走 preview/确认流程，避免扩大本次权限变更范围。 |
| `command` | `run_bash`、`run_temp_script` | 禁止明显危险命令，高风险命令需要确认，支持 timeout 和输出截断。 |

## 文件访问边界

当前文件工具通过 `resolve_workspace_path()` 约束路径：

- 相对路径会解析到当前 workspace。
- workspace 外路径默认拒绝。
- 系统临时目录作为受控例外，用于临时测试和工具执行。
- `.gitignore`、依赖目录、构建产物等噪声目录在列表工具中默认过滤。

## 文件读写执行策略

当前策略是：`read_file`、`write_file`、`replace_in_file` 直接执行，不等待用户确认。这是用户明确要求，后续不要再改回“必须确认”模式，除非用户再次明确要求。

直接执行不等于放开安全边界：

1. `read_file` 仍限制在 workspace 内，并支持 query、line window、分片和大文件概要。
2. `write_file` / `replace_in_file` 执行前仍记录 diff preview，供 TUI、trace 和日志审计。
3. `replace_in_file` 依赖精确 `old_text`、上下文校验和替换次数控制，避免误替换。
4. `write_file` 覆盖已有大文件仍要求 `allow_full_rewrite=true`，默认建议使用 `replace_in_file`。
5. `--dry-run` 模式只生成预览和 trace，不会真实落盘。
6. workspace 外路径、受保护路径和不满足代码修改门禁的写入仍会拒绝。

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
- 文件读写按用户要求直执行，但保留 diff 审计、workspace 边界、dry-run 和代码质量门禁。
- 命令仍是 human-in-the-loop，不是静默执行。
- 当前是本地 MVP，强沙箱和多租户权限是生产化演进项。
