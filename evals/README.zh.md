# Vora Eval 说明

## 目的

单元测试验证模块行为，eval 验证 Agent 关键链路是否仍满足产品级约束。这个目录用于说明 Vora 的 AI 工程质量体系：不是只测函数是否返回正确，而是验证 Agent 是否遵守质量门禁、安全边界和上下文完整性；在面试或技术评审中也可以作为质量体系证据。

## 当前 eval 覆盖

| 类别 | 用例 | 验证点 |
|---|---|---|
| Reflection | `reflection_rejects_unvalidated_code` | 代码任务没有测试证据时不能通过 Reflection。 |
| Reflection | `reflection_accepts_validated_code` | 代码任务有测试证据时会执行 pytest gate 并通过。 |
| Reflection | `non_code_task_bypasses_pytest_gate` | 非代码任务当前版本不运行 pytest gate，为后续结构化验收预留。 |
| Memory | `sensitive_memory_is_rejected` | API key、token、password 等敏感信息不会写入长期记忆。 |
| Context | `tool_exchange_integrity_is_enforced` | assistant tool call 和 tool result 必须成组完整。 |
| Tools | `scheduler_batches_read_only_tools` | 无依赖只读工具可以进入同一并行批次。 |
| Security | `path_escape_is_rejected` | 文件工具拒绝 workspace 外路径。 |
| Security | `write_file_executes_directly_with_preview` | `write_file` 按用户要求直接执行，并保留预览能力。 |
| Security | `dangerous_command_is_rejected` | 明确危险的系统命令必须被本地规则拒绝。 |

## 运行

```bash
python evals/run_evals.py
python evals/run_evals.py --json-report eval-report.json --markdown-report eval-report.md
```

用例元数据统一声明在 `evals/cases.zh.json`。成功时输出 JSON 报告，`failed` 为 0；失败时脚本返回非 0 exit code。CI 同时产出机器可读 JSON 和人工可读 Markdown 报告。

## 后续扩展

下一版可补充：

- 非代码任务结构化验收 case。
- 真实 demo 任务集，例如项目分析、文件修改审计、代码修复后测试。
- LLM 输出稳定性评测，例如工具调用成功率、重试率、高风险命令确认率。
- 更完整的安全违规率评测，例如 Prompt injection、符号链接逃逸和资源耗尽。
