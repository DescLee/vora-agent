import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from vora.memory import MemoryManager
from vora.logging import project_memory_path
from vora.models import LoopLimits, Message, Observation, PlanStep, SessionState, TaskState, TraceEvent
from vora.prompt_tui import (
    SHIFT_ENTER_SEQUENCES,
    PromptTui,
    PromptTuiOptions,
    build_display_line_starts,
    build_line_starts,
    format_artifact,
    format_current_action,
    format_context_usage,
    format_inline_args,
    format_latest_activity,
    format_message_block,
    line_number_for_position,
    format_phase_label,
    format_messages,
    format_process,
    format_trace_data,
    format_transcript,
    format_status,
    format_welcome,
    install_shift_enter_mapping,
    insert_newline,
    render_markdown_result,
    style_confirmation_fragments,
    style_output_fragments,
)


def test_format_messages_renders_chinese_user_content(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("帮我写一份 AI 调研报告。"))
    session.messages.append(Message.agent("可以，我先整理结构。"))

    rendered = format_messages(session)

    assert "│  帮我写一份 AI 调研报告。" in rendered
    assert "Agent: 可以，我先整理结构。" in rendered


def test_format_messages_collapses_tool_file_content_into_summary(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(
        Message.tool(
            "read README.md\n\ncontent:\n# vora\n\nmore text",
            tool_call_id="call-read",
        )
    )

    rendered = format_messages(session)

    assert "工具: [README.md 文件内容获取成功]" in rendered
    assert "# vora" not in rendered
    assert "content:" not in rendered


def test_format_messages_omits_internal_long_term_memory(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("请你看下项目"))
    session.messages.append(Message.system("长期记忆:\n- 旧项目优化建议"))
    session.messages.append(Message.agent("当前回答"))

    rendered = format_messages(session)

    assert "旧项目优化建议" not in rendered
    assert "长期记忆" not in rendered
    assert "请你看下项目" in rendered
    assert "当前回答" in rendered


def test_format_messages_adds_padding_and_background_marker_for_user_message(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("帮我写一份 AI 调研报告。"))

    rendered = format_messages(session)

    assert "┌─ 你 " in rendered
    assert "│  帮我写一份 AI 调研报告。" in rendered
    assert "└" in rendered


def test_format_message_block_wraps_user_text_with_light_panel() -> None:
    rendered = format_message_block(Message.user("第一行\n第二行"))

    assert rendered.splitlines() == [
        "┌─ 你 ───────────────────────────────────",
        "│  第一行",
        "│  第二行",
        "└────────────────────────────────────────",
    ]


def test_format_welcome_explains_limits_and_controls(tmp_path: Path) -> None:
    task = TaskState.create(goal="demo", cwd=tmp_path)
    env_path = tmp_path / ".env"
    welcome = format_welcome(
        task.limits,
        llm_model="deepseek-v4-flash",
        llm_configured=True,
        llm_config_source=str(env_path),
    )

    assert "Vora TUI" in welcome
    assert "直接输入任务开始连续对话" in welcome
    assert "当前模型：deepseek-v4-flash" in welcome
    assert f"配置来源：{env_path}" in welcome
    assert "输入 `/help` 查看常用入口" in welcome
    assert "\n常用入口\n" not in welcome
    assert "默认 TUI 入口：vora --cwd ." not in welcome
    assert "一次性任务：vora run" not in welcome
    assert "查看历史会话：vora list --cwd ." not in welcome
    assert "恢复会话：vora resume <session_id>" not in welcome
    assert "MCP 配置：vora mcp list --cwd ." not in welcome
    assert "Skills 管理：vora skills list --cwd ." not in welcome
    assert "写入文件前会展示 diff 并等待确认" not in welcome
    assert "~/.vora/projects/<project_key>" not in welcome
    assert "\n运行限制\n" not in welcome
    assert "工程循环上限：3 轮" not in welcome
    assert "ReAct 循环上限：99 轮" not in welcome
    assert "Reflection 循环上限：3 轮" not in welcome
    assert "工具执行时间：不限制" not in welcome
    assert "工具超时上限" not in welcome
    assert "单轮运行超时" not in welcome
    assert "压缩上下文" not in welcome
    assert "/compact" not in welcome
    assert "/save-context" not in welcome
    assert "/help" in welcome
    assert "Enter：发送" not in welcome
    assert "Ctrl+J：换行" not in welcome
    assert "Tab：切换输入区和输出区" not in welcome
    assert "Ctrl+C：退出" not in welcome
    assert "vora tui" not in welcome


def test_format_welcome_warns_when_llm_config_is_missing(tmp_path: Path) -> None:
    task = TaskState.create(goal="demo", cwd=tmp_path)

    welcome = format_welcome(task.limits, llm_configured=False)

    assert "未找到可用 LLM 配置" in welcome
    assert "当前目录 `.env`" in welcome
    assert "LLM_PROVIDER" in welcome
    assert "LLM_BASE_URL" in welcome
    assert "LLM_API_KEY" in welcome
    assert "当前模型" not in welcome


def test_prompt_tui_welcome_shows_current_model(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai-compatible\n"
        "LLM_BASE_URL=http://localhost:1234/v1\n"
        "LLM_API_KEY=test-key\n"
        "LLM_MODEL=qwen-test\n",
        encoding="utf-8",
    )
    for key in ["LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_TIMEOUT_SECONDS"]:
        monkeypatch.delenv(key, raising=False)

    tui = PromptTui(cwd=tmp_path)

    assert "正在加载会话与模型信息" in tui.output.text


def test_prompt_tui_startup_initialization_updates_welcome_model(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai-compatible\n"
        "LLM_BASE_URL=http://localhost:1234/v1\n"
        "LLM_API_KEY=test-key\n"
        "LLM_MODEL=qwen-test\n",
        encoding="utf-8",
    )
    for key in ["LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_TIMEOUT_SECONDS"]:
        monkeypatch.delenv(key, raising=False)

    tui = PromptTui(cwd=tmp_path)
    tui.complete_startup_initialization()

    assert "当前模型：qwen-test" in tui.output.text
    assert f"配置来源：{env_path}" in tui.output.text
    assert "未找到可用 LLM 配置" not in tui.output.text


def test_prompt_tui_constructor_does_not_resolve_model_context_limit(monkeypatch, tmp_path: Path) -> None:
    def fail_if_called(self):  # noqa: ANN001
        raise AssertionError("model context should resolve after TUI is visible")

    monkeypatch.setattr("vora.runtime.AgentRuntime.resolve_model_context_limit", fail_if_called)

    tui = PromptTui(cwd=tmp_path)

    assert "正在加载会话与模型信息" in tui.output.text


def test_prompt_tui_welcome_warns_when_llm_config_is_missing(tmp_path: Path, monkeypatch) -> None:
    from vora.config import AppConfig

    monkeypatch.setattr("vora.prompt_tui.AppConfig.from_env", lambda *args, **kwargs: AppConfig())

    tui = PromptTui(
        options=PromptTuiOptions(cwd=tmp_path, limits=LoopLimits()),
    )
    tui.complete_startup_initialization()

    assert "未找到可用 LLM 配置" in tui.output.text
    assert "LLM_PROVIDER" in tui.output.text
    assert "当前模型" not in tui.output.text


def test_format_artifact_renders_active_task_result(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    task.result = "报告正文"
    session.active_task = task

    artifact = format_artifact(session)
    assert "完成摘要" not in artifact
    assert "结果正文" not in artifact
    assert "状态：" not in artifact
    assert "执行步数" not in artifact
    assert "工具观察" not in artifact
    assert "报告正文" in artifact


def test_render_markdown_result_displays_terminal_friendly_markdown() -> None:
    rendered = render_markdown_result("# 标题\n\n- **重点** 内容\n\n```python\nprint(1)\n```")

    assert "# 标题" not in rendered
    assert "**重点**" not in rendered
    assert "标题" in rendered
    assert "• 重点 内容" in rendered
    assert "print(1)" in rendered


def test_format_artifact_renders_result_markdown(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    task.result = "# 报告\n\n- **结论**：可行"
    session.active_task = task

    artifact = format_artifact(session)

    assert "# 报告" not in artifact
    assert "**结论**" not in artifact
    assert "报告" in artifact
    assert "• 结论：可行" in artifact


def test_output_fragments_style_process_section_with_dim_text() -> None:
    text = "\n\n".join(
        [
            "────────────────────────────────────────\n用户问题\n总结项目",
            "────────────────────────────────────────\n执行过程\n步骤概览\n- 调用工具",
            "────────────────────────────────────────\n最终产物\n结果正文\n报告",
        ]
    )

    fragments = style_output_fragments(text)
    process_fragment = next(fragment for fragment in fragments if "步骤概览" in fragment[1])
    artifact_fragment = next(fragment for fragment in fragments if "报告" in fragment[1])

    assert "class:process" in process_fragment[0]
    assert "class:process" not in artifact_fragment[0]


def test_output_fragments_styles_reasoning_line_brighter_than_process_text() -> None:
    fragments = style_output_fragments(
        "执行过程\n"
        "────────────────────────────────────────\n"
        "• 需要读取项目文件。\n"
        "● Ran 1.1 read_file(call-read)\n"
    )

    reasoning_fragment = next(fragment for fragment in fragments if "需要读取项目文件" in fragment[1])

    assert reasoning_fragment[0] == "class:process.reasoning"


def test_output_fragments_styles_wrapped_reasoning_continuation_lines() -> None:
    fragments = style_output_fragments(
        "执行过程\n"
        "────────────────────────────────────────\n"
        "• 第一行推理内容\n"
        "第二行推理内容\n"
        "● Ran 1.1 read_file(call-read)\n"
    )

    continuation_fragment = next(fragment for fragment in fragments if "第二行推理内容" in fragment[1])
    tool_fragment = next(fragment for fragment in fragments if "Ran" in fragment[1])

    assert continuation_fragment[0] == "class:process.reasoning"
    assert tool_fragment[0] == "class:process.tool"


def test_output_fragments_color_diff_additions_and_removals_in_process_section() -> None:
    text = "\n\n".join(
        [
            "────────────────────────────────────────\n执行过程\n- 1 replace_in_file(call) 变更预览:\n  --- a/app.py\n  +++ b/app.py\n  @@ -1 +1 @@\n  -old\n  +new\n  context",
            "────────────────────────────────────────\n最终产物\n+not diff",
        ]
    )

    fragments = style_output_fragments(text)
    styles_by_text = {fragment_text.strip(): style for style, fragment_text in fragments if fragment_text.strip()}

    assert "bg:" in styles_by_text["-old"]
    assert "#7f1d1d" in styles_by_text["-old"]
    assert "bg:" in styles_by_text["+new"]
    assert "#064e3b" in styles_by_text["+new"]
    assert "bg:" in styles_by_text["--- a/app.py"]
    assert "bg:" in styles_by_text["+++ b/app.py"]
    assert styles_by_text["+not diff"] == ""


def test_output_fragments_color_diff_when_process_text_is_rendered_standalone() -> None:
    fragments = style_output_fragments(
        "- 1 replace_in_file(call) 变更预览:\n"
        "  --- a/app.py\n"
        "  +++ b/app.py\n"
        "  @@ -1 +1 @@\n"
        "  -old\n"
        "  +new\n"
    )

    styles_by_text = {fragment_text.strip(): style for style, fragment_text in fragments if fragment_text.strip()}

    assert "#7f1d1d" in styles_by_text["-old"]
    assert "#064e3b" in styles_by_text["+new"]


def test_format_process_renders_trace_events(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    task.result = "报告正文"
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool read_file finished: failed",
            data={"tool_name": "read_file", "error_code": "INVALID_TOOL_PARAMS"},
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "工具活动" in process
    assert "read_file(unknown)" in process
    assert "  └ Tool read_file finished: failed" in process
    assert "Tool read_file finished: failed" in process
    assert "最近过程（折叠）" not in process


def test_format_process_shows_immediate_thinking_state_before_first_trace(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.active_task = TaskState.create(goal="看下北京天气", cwd=tmp_path)

    process = format_process(session)

    assert "• 正在分析请求" in process
    assert "工具活动" not in process
    assert "暂无工具调用" not in process


def test_format_context_usage_counts_current_session_messages(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="统计上下文", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="工具结果很多很多很多很多很多很多很多很多很多很多",
            data={"content_preview": "X" * 1000},
        )
    )
    session.messages.append(Message.user("一二三四五六七八九十"))
    session.active_task = task
    task.limits.max_estimated_tokens = 100
    usage = format_context_usage(session)

    assert usage == "当前上下文 5.0%"


def test_format_context_usage_prefers_latest_llm_prompt_tokens(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="统计上下文", cwd=tmp_path)
    task.model_context_limit = 1_000
    session.current_context_tokens = 250
    task.limits.max_estimated_tokens = 100
    session.messages.append(Message.user("x" * 10))
    session.active_task = task

    usage = format_context_usage(session)

    assert usage == "当前上下文 25.0%"


def test_latest_activity_formats_latest_event_for_status_bar(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="react",
            message="ReAct iteration 1 started",
            data={"iteration": 1},
        )
    )
    session.active_task = task

    status = format_status(session)

    assert format_latest_activity(task) == "ReAct：第 1 轮开始"
    assert status == "状态 正在执行 | 当前上下文 0.0%"


def test_format_process_groups_current_step_tool_calls_and_observations(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.step_count = 2
    task.plan = [
        PlanStep(description="扫描工作目录并识别项目结构", intent="research", status="done"),
        PlanStep(description="读取关键文档", intent="research", status="running"),
    ]
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={
                "iteration": 1,
                "tool_calls": [
                    {
                        "id": "call-read",
                        "name": "read_file",
                        "args": {"path": "README.md"},
                    }
                ],
            },
        )
    )
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool read_file finished: ok",
            data={
                "tool_call_id": "call-read",
                "tool_name": "read_file",
                "summary": "read README.md",
                "content_preview": "# demo project",
            },
        )
    )
    task.observations.append(
        Observation(
            tool_call_id="call-read",
            ok=True,
            summary="read README.md",
            content="# demo project",
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "步骤概览" not in process
    assert "第 2 步" not in process
    assert "执行计划" not in process
    assert "[已完成] 扫描工作目录并识别项目结构" not in process
    assert "[进行中] 读取关键文档" not in process
    assert "动作" not in process
    assert "LLM 回合" not in process
    assert "工具调度" not in process
    assert "共 1 个批次" not in process
    assert "第 1 批" not in process
    assert "● Ran 1.1 read_file README.md" in process
    assert "  └ read README.md" in process
    assert "# demo project" not in process
    assert "最近过程（折叠）" not in process


def test_format_process_orders_llm_content_before_matching_tool_call_and_result(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={
                "iteration": 1,
                "content_preview": "我需要先确认 README 内容。",
                "tool_calls": [{"id": "call-read", "name": "read_file", "args": {"path": "README.md"}}],
            },
        )
    )
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool read_file finished: ok",
            data={
                "iteration": 1,
                "tool_call_id": "call-read",
                "tool_name": "read_file",
                "ok": True,
                "summary": "read README.md",
            },
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "LLM 回合" not in process
    assert "工具调度" not in process
    assert "第 1 批" not in process
    assert "我需要先确认 README 内容。" not in process
    assert "LLM 返回" not in process
    assert "- 已返回" not in process
    assert "● Ran 1.1 read_file README.md" in process
    assert "  └ read README.md" in process


def test_format_process_groups_tool_call_args_and_result(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={
                "iteration": 1,
                "tool_calls": [{"id": "call-read", "name": "read_file", "args": {"path": "README.md", "query": "Vora"}}],
            },
        )
    )
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool read_file finished: ok",
            data={
                "iteration": 1,
                "tool_call_id": "call-read",
                "tool_name": "read_file",
                "ok": True,
                "summary": "found 2 match(es)",
            },
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "● Ran 1.1 read_file README.md" in process
    assert "  └ found 2 match(es)" in process
    assert "调用 read_file(call-read)" not in process
    assert "read_file(call-read) 成功:" not in process


def test_format_process_uses_compact_tool_activity_layout(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="检查构建产物", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 2 tool call(s)",
            data={
                "iteration": 10,
                "tool_calls": [
                    {
                        "id": "call-build",
                        "name": "run_bash",
                        "args": {"command": "pnpm run build:UAT 2>&1 | grep -E 'chunk|Chunk'"},
                    },
                    {
                        "id": "call-read",
                        "name": "read_file",
                        "args": {"path": "vite.config.ts"},
                    },
                ],
            },
        )
    )
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool run_bash finished: ok",
            data={
                "iteration": 10,
                "tool_call_id": "call-build",
                "tool_name": "run_bash",
                "ok": True,
                "summary": "(no output)",
            },
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "● Ran 10.1 pnpm run build:UAT 2>&1 | grep -E 'chunk|Chunk'" in process
    assert "  └ (no output)" in process
    assert "● Ran 10.2 read_file vite.config.ts" in process
    assert "  └ waiting" in process
    assert "参数:" not in process
    assert "工具结果:" not in process


def test_format_process_keeps_diff_preview_under_tool_result(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改代码", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={
                "iteration": 1,
                "tool_calls": [{"id": "call-write", "name": "write_file", "args": {"path": "src/app.py"}}],
            },
        )
    )
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool write_file pending confirmation",
            data={
                "iteration": 1,
                "tool_call_id": "call-write",
                "tool_name": "write_file",
                "ok": False,
                "summary": "waiting for confirmation",
                "diff_preview": "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new",
            },
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "● Ran 1.1 write_file src/app.py" in process
    assert "  └ failed，waiting for confirmation" in process
    assert "变更预览:" in process
    assert "--- a/src/app.py" in process
    assert "+new" in process


def test_format_process_renders_llm_reasoning_content(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={
                "iteration": 1,
                "reasoning_content": "需要先读取 README 和 package.json 判断项目类型。",
                "tool_calls": [{"id": "call-read", "name": "read_file", "args": {"path": "README.md"}}],
            },
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "LLM 返回" not in process
    assert "- 已返回" not in process
    assert "• 需要先读取 README 和 package.json 判断项目类型。" in process
    assert "• 推理内容" not in process
    assert "────────────────────────────────────────" in process
    assert "需要先读取 README 和 package.json 判断项目类型。" in process
    assert "● Ran 1.1 read_file README.md" in process
    assert "  └ waiting" in process


def test_format_process_keeps_reasoning_content_visible_without_placeholder(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={
                "iteration": 1,
                "reasoning_content": "现在已经看到了项目概况，接下来需要继续检查 context.py 和 executor.py。",
                "tool_calls": [{"id": "call-read", "name": "read_file", "args": {"path": "README.md"}}],
            },
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "现在已经看到了项目概况" in process
    assert "模型已生成推理内容" not in process


def test_format_process_summarizes_trace_without_raw_nested_json(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 2 tool call(s)",
            data={
                "iteration": 10,
                "content_preview": "",
                "tool_calls": [
                    {"id": "call-list", "name": "list_files", "args": {"path": "."}},
                    {"id": "call-read", "name": "read_file", "args": {"path": "README.md"}},
                ],
            },
        )
    )
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool read_file finished: ok",
            data={
                "iteration": 10,
                "tool_call_id": "call-read",
                "tool_name": "read_file",
                "args": {"path": "README.md"},
                "ok": True,
                "summary": "read README.md",
                "content_preview": "# demo",
            },
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "LLM 回合" not in process
    assert "工具调度" not in process
    assert "共 1 个批次" not in process
    assert "第 1 批" not in process
    assert "● Ran 10.1 list_files ." in process
    assert "  └ waiting" in process
    assert "● Ran 10.2 read_file README.md" in process
    assert "  └ read README.md" in process
    assert '"tool_calls"' not in process
    assert "{'tool_calls'" not in process
    assert "[{" not in process


def test_format_process_groups_tool_returns_by_planned_batch(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 3 tool call(s)",
            data={
                "iteration": 10,
                "content_preview": "",
                "tool_calls": [
                    {"id": "call-list", "name": "list_files", "args": {"path": "."}},
                    {"id": "call-read", "name": "read_file", "args": {"path": "README.md"}},
                    {"id": "call-docs", "name": "read_file", "args": {"path": "docs/design.md"}},
                ],
            },
        )
    )
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool scheduler planned 2 batch(es)",
            data={"iteration": 10, "batches": [["call-list", "call-read"], ["call-docs"]]},
        )
    )
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool list_files finished: ok",
            data={"iteration": 10, "tool_call_id": "call-list", "tool_name": "list_files", "ok": True, "summary": "found 3 files"},
        )
    )
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool read_file finished: ok",
            data={"iteration": 10, "tool_call_id": "call-read", "tool_name": "read_file", "ok": True, "summary": "read README.md"},
        )
    )
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool read_file finished: ok",
            data={"iteration": 10, "tool_call_id": "call-docs", "tool_name": "read_file", "ok": True, "summary": "read docs/design.md"},
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "LLM 回合" not in process
    assert "工具调度" not in process
    assert "共 2 个批次" not in process
    assert "第 1 批" not in process
    assert "第 2 批" not in process
    assert "● Ran 10.1 list_files ." in process
    assert "  └ found 3 files" in process
    assert "● Ran 10.2 read_file README.md" in process
    assert "  └ read README.md" in process
    assert "● Ran 10.3 read_file docs/design.md" in process
    assert "  └ read docs/design.md" in process


def test_format_process_shows_llm_returned_content(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={
                "content_preview": "我需要先确认项目结构，所以准备读取文件列表。",
                "tool_calls": [{"id": "call-list", "name": "list_files", "args": {"path": "."}}],
            },
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "LLM 返回" not in process
    assert "- 已返回" not in process
    assert "我需要先确认项目结构，所以准备读取文件列表。" not in process
    assert "返回内容" not in process


def test_format_tool_return_without_ok_flag_uses_neutral_status(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool read_file finished: ok",
            data={
                "tool_call_id": "call-read",
                "tool_name": "read_file",
                "summary": "read README.md",
                "content_preview": "x" * 260,
            },
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "● Ran read_file(call-read)" in process
    assert "  └ read README.md" in process
    assert "read_file(call-read) 失败" not in process
    assert "x" * 260 not in process
    assert "返回预览" not in process


def test_format_inline_args_uses_readable_user_facing_style() -> None:
    args = format_inline_args({"path": "README.md", "limit": 10, "confirmed": True})

    assert args == "path: README.md | limit: 10 | confirmed: true"
    assert "path='README.md'" not in args


def test_format_trace_data_shortens_large_nested_values() -> None:
    data = format_trace_data(
        {
            "tool_calls": [
                {
                    "id": "call-read",
                    "name": "read_file",
                    "args": {"path": "README.md", "content": "x" * 240},
                }
            ],
            "content_preview": "第一行\n" + "很长" * 160,
        }
    )

    assert "tool_calls:" in data
    assert "content_preview:" in data
    assert "x" * 240 not in data
    assert "很长" * 160 not in data
    assert "…" in data


def test_format_phase_label_maps_task_status_for_users(tmp_path: Path) -> None:
    task = TaskState.create(goal="总结项目", cwd=tmp_path)

    task.status = "planning"
    assert format_phase_label(task) == "规划任务"

    task.status = "acting"
    assert format_phase_label(task) == "调用工具"

    task.status = "reflecting"
    assert format_phase_label(task) == "反思校验"

    task.status = "done"
    assert format_phase_label(task) == "已完成"


def test_format_status_shows_reflection_reason_as_latest_activity(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="reflection",
            message="Reflection decided replan: 需要补充技术架构说明",
            data={
                "decision": "replan",
                "reason": "需要补充技术架构说明",
                "draft_preview": "草稿",
            },
        )
    )
    session.active_task = task

    status = format_status(session)

    assert status == "状态 正在执行 | 当前上下文 0.0%"


def test_format_process_shows_reflection_decisions_and_reasons(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修复测试", cwd=tmp_path)
    task.trace_events.extend(
        [
            TraceEvent(
                phase="reflection",
                message="Reflection decided local_update: 缺少测试输出",
                data={
                    "decision": "local_update",
                    "accepted": False,
                    "reason": "缺少测试输出，需要补充 pytest 运行结果。",
                    "draft_preview": "代码修改已完成，测试已通过。",
                },
            ),
            TraceEvent(
                phase="reflection",
                message="Reflection decided local_update: 仍未看到测试结果",
                data={
                    "decision": "local_update",
                    "accepted": False,
                    "reason": "仍未看到测试结果，不能接受当前草稿。",
                },
            ),
        ]
    )
    session.active_task = task

    process = format_process(session, full_history=True)

    assert "反思校验" not in process
    assert "local_update" not in process
    assert "未通过" not in process
    assert "缺少测试输出，需要补充 pytest 运行结果。" not in process
    assert "仍未看到测试结果，不能接受当前草稿。" not in process
    assert "代码修改已完成，测试已通过。" not in process


def test_format_process_highlights_phase_and_current_action(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.status = "acting"
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={"tool_calls": [{"id": "call-read", "name": "read_file", "args": {"path": "README.md"}}]},
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "阶段：调用工具" not in process
    assert "动作：准备调用工具 read_file(call-read)" not in process
    assert "● Ran 1 read_file README.md" in process


def test_format_process_hides_internal_plan_details(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结 diff", cwd=tmp_path)
    task.plan_reasoning_content = "这里是一大段规划理由，包含很多过程性解释，不应该展示在 TUI 执行计划里。"
    task.plan = [
        PlanStep(description="执行 git diff 查看工作区变更", intent="automation", status="done"),
        PlanStep(description="汇总所有 diff", intent="report", status="done"),
    ]
    session.active_task = task

    process = format_process(session)

    assert "执行计划" not in process
    assert "规划理由" not in process
    assert "过程性解释" not in process
    assert "[已完成] 执行 git diff 查看工作区变更" not in process
    assert "[已完成] 汇总所有 diff" not in process


def test_format_process_shows_observations_when_trace_tool_return_is_missing(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.observations.append(
        Observation(
            tool_call_id="call-read",
            ok=True,
            summary="read README.md",
            content="# demo project",
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "● Ran unknown(call-read)" in process
    assert "  └ read README.md" in process
    assert "call-read" in process
    assert "read README.md" in process
    assert "# demo project" not in process


def test_format_process_hides_react_events_from_tui_transcript(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="长过程", cwd=tmp_path)
    for index in range(12):
        task.trace_events.append(
            TraceEvent(
                phase="react",
                message=f"event {index}",
                data={"index": index},
            )
        )
    session.active_task = task

    process = format_process(session)

    assert "event 11" not in process
    assert "最近过程（折叠）" not in process
    assert "已折叠" not in process


def test_format_process_redacts_sensitive_trace_data(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM error",
            data={"api_key": "sk-live-secret", "detail": "password=abc123"},
        )
    )
    session.active_task = task

    process = format_process(session)

    assert "sk-live-secret" not in process
    assert "password=abc123" not in process
    assert "LLM 回合" not in process
    assert "最近过程（折叠）" not in process


def test_format_transcript_shows_process_while_running(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("总结项目"))
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(TraceEvent(phase="tool", message="Tool list_files finished: ok"))
    session.active_task = task

    transcript = format_transcript(session, show_process=True)

    assert "────────────────" in transcript
    assert "会话信息" in transcript
    assert f"Run ID: {task.run_id}" in transcript
    assert "用户问题" in transcript
    assert "总结项目" in transcript
    assert "对话记录" not in transcript
    assert "执行过程" in transcript
    assert "步骤概览" not in transcript
    assert "Tool list_files finished: ok" not in transcript
    assert "产物" not in transcript


def test_format_transcript_keeps_process_for_final_artifact(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("总结项目"))
    session.messages.append(Message.agent("最终总结"))
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.result = "最终总结"
    task.trace_events.append(TraceEvent(phase="tool", message="Tool list_files finished: ok"))
    session.active_task = task

    transcript = format_transcript(session, show_process=False)

    assert "对话" not in transcript
    assert "执行过程" in transcript
    assert "产物" in transcript
    assert "最终总结" in transcript
    assert transcript.count("最终总结") == 1
    assert "工具活动" in transcript
    assert "最近过程（折叠）" not in transcript


def test_format_transcript_final_artifact_keeps_full_process_history(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("总结项目"))
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.result = "最终总结"
    for index in range(20):
        task.trace_events.append(
            TraceEvent(
                phase="tool",
                message=f"Tool read_file finished: ok {index}",
                data={
                    "tool_name": "read_file",
                    "tool_call_id": f"call-{index}",
                    "ok": True,
                    "summary": f"read file {index}",
                },
            )
        )
    session.active_task = task

    transcript = format_transcript(session, show_process=False)

    assert "read file 0" in transcript
    assert "read file 19" in transcript
    assert "仅展示最近" not in transcript


def test_format_status_hides_send_hints(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.model_context_limit = 1_000_000

    status = format_status(session)

    assert status == "就绪 | 上下文窗口上限 1,000,000"
    assert "Enter 发送" not in status
    assert "Shift+Enter 换行" not in status


def test_format_status_shows_context_usage(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.limits.max_estimated_tokens = 100
    session.model_context_limit = 100
    session.active_task = task
    session.messages.append(Message.user("x" * 50))

    status = format_status(session)

    assert "当前上下文 25%" not in status
    assert "当前上下文 25.0%" in status
    assert status.endswith("上下文窗口上限 100")
    assert "最新请求" not in status
    assert format_context_usage(session) == "当前上下文 25.0%"


def test_prompt_tui_renders_confirmation_overlay(tmp_path: Path) -> None:
    from vora.models import PendingConfirmation

    session = SessionState.create(cwd=tmp_path)
    session.pending_confirmation = PendingConfirmation(
        tool_name="write_file",
        tool_call_id="call-write",
        summary="即将修改 notes.txt",
        prompt="即将修改 notes.txt",
        diff_preview="--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-old\n+new\n",
    )
    tui = PromptTui(cwd=tmp_path, initial_session=session)

    tui.refresh_confirmation_panel()

    assert tui.should_show_confirmation_overlay() is True
    assert "确认写入" in tui.confirmation_panel_view.text
    assert "变更预览" in tui.confirmation_panel_view.text
    assert "+new" in tui.confirmation_panel_view.text


def test_format_process_shows_replace_diff_preview(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="修改代码", cwd=tmp_path)
    task.trace_events.extend(
        [
            TraceEvent(
                phase="llm",
                message="LLM requested 1 tool call(s)",
                data={
                    "tool_calls": [
                        {
                            "id": "call-replace",
                            "name": "replace_in_file",
                            "args": {"path": "app.py"},
                        }
                    ]
                },
            ),
            TraceEvent(
                phase="tool",
                message="Tool diff preview",
                data={
                    "message_type": "diff_preview",
                    "tool_name": "replace_in_file",
                    "tool_call_id": "call-replace",
                    "diff_preview": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",
                },
            ),
        ]
    )
    session.active_task = task

    process = format_process(session)

    assert "变更预览" in process
    assert "--- a/app.py" in process
    assert "-old" in process
    assert "+new" in process


def test_confirmation_fragments_color_diff_additions_and_removals() -> None:
    fragments = style_confirmation_fragments(
        "确认写入\n\n变更预览\n\n--- a/file.txt\n+++ b/file.txt\n-old\n+new\n context\n"
    )

    styles_by_text = {text.strip(): style for style, text in fragments if text.strip()}

    assert "bg:" in styles_by_text["--- a/file.txt"]
    assert "bg:" in styles_by_text["+++ b/file.txt"]
    assert "#7f1d1d" in styles_by_text["-old"]
    assert "#064e3b" in styles_by_text["+new"]


def test_prompt_tui_refreshes_confirmation_when_pending_appears_during_run(tmp_path: Path) -> None:
    from vora.models import PendingConfirmation

    session = SessionState.create(cwd=tmp_path)
    tui = PromptTui(cwd=tmp_path, initial_session=session)

    session.pending_confirmation = PendingConfirmation(
        tool_name="write_file",
        tool_call_id="call-write",
        summary="即将修改 notes.txt",
        prompt="即将修改 notes.txt",
        diff_preview="+new",
    )
    tui.manager.current = session

    tui.render_progress()

    assert "确认写入" in tui.confirmation_panel_view.text
    assert "+new" in tui.confirmation_panel_view.text


def test_prompt_tui_keeps_confirmation_overlay_visible_while_processing(tmp_path: Path) -> None:
    from vora.models import PendingConfirmation

    session = SessionState.create(cwd=tmp_path)
    session.pending_confirmation = PendingConfirmation(
        tool_name="write_file",
        tool_call_id="call-write",
        summary="即将修改 notes.txt",
        prompt="即将修改 notes.txt",
    )
    tui = PromptTui(cwd=tmp_path, initial_session=session)
    tui.confirmation_in_progress = True

    assert tui.should_show_confirmation_overlay() is True


def test_prompt_tui_scrolls_long_confirmation_preview(tmp_path: Path) -> None:
    from vora.models import PendingConfirmation

    session = SessionState.create(cwd=tmp_path)
    session.pending_confirmation = PendingConfirmation(
        tool_name="write_file",
        tool_call_id="call-write",
        summary="即将修改 notes.txt",
        prompt="即将修改 notes.txt",
        diff_preview="\n".join(f"+line {index}" for index in range(60)),
    )
    tui = PromptTui(cwd=tmp_path, initial_session=session)

    tui.refresh_confirmation_panel()
    initial_scroll_top = tui.confirmation_panel_view.scroll_top

    tui.scroll_confirmation(5)

    assert initial_scroll_top == 0
    assert tui.confirmation_panel_view.scroll_top > initial_scroll_top


def test_confirm_pending_confirmation_starts_background_follow_up(tmp_path: Path) -> None:
    from vora.models import PendingConfirmation

    session = SessionState.create(cwd=tmp_path)
    session.pending_confirmation = PendingConfirmation(
        tool_name="write_file",
        tool_call_id="call-write",
        summary="即将修改 notes.txt",
        prompt="即将修改 notes.txt",
        diff_preview="+large diff",
    )
    tui = PromptTui(cwd=tmp_path, initial_session=session)
    started = []

    tui.start_agent_turn = lambda content, confirmation_turn=False: started.append((content, confirmation_turn))  # type: ignore[method-assign]

    tui.confirm_pending_confirmation()

    assert tui.confirmation_in_progress is True
    assert "正在执行写入和后续流程" in tui.confirmation_panel_view.text
    assert "+large diff" not in tui.confirmation_panel_view.text
    assert started == [("确认", True)]


def test_prompt_tui_enter_cancel_text_rejects_pending_confirmation(tmp_path: Path) -> None:
    from vora.models import PendingConfirmation

    session = SessionState.create(cwd=tmp_path)
    session.pending_confirmation = PendingConfirmation(
        tool_name="write_file",
        tool_call_id="call-write",
        summary="即将修改 notes.txt",
        prompt="即将修改 notes.txt",
        diff_preview="+large diff",
    )
    tui = PromptTui(cwd=tmp_path, initial_session=session)
    started = []

    tui.start_agent_turn = lambda content, confirmation_turn=False: started.append((content, confirmation_turn))  # type: ignore[method-assign]

    tui.input.text = "取消"
    tui.submit_confirmation_input(tui.input.text)

    assert tui.manager.current.pending_confirmation is None
    assert tui.confirmation_in_progress is False
    assert tui.input.text == ""
    assert started == []
    assert tui.manager.current.messages[-1].content == "用户拒绝了待确认写入。"


def test_format_status_context_usage_includes_active_task_process(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.limits.max_estimated_tokens = 10_000
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested tools",
            data={"content_preview": "x" * 4_000, "tool_calls": [{"id": "call-read", "name": "read_file"}]},
        )
    )
    task.observations.append(
        Observation(
            tool_call_id="call-read",
            ok=True,
            summary="read README.md",
            content="y" * 4_000,
        )
    )
    session.active_task = task
    session.messages.append(Message.user("hi"))

    assert format_context_usage(session) == "当前上下文 0.0%"


def test_format_status_describes_current_step_while_running(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.step_count = 3
    task.status = "acting"
    session.active_task = task

    status = format_status(session)

    assert "正在执行" in status
    assert "第 3/12 步" not in status
    assert "ReAct 上限" not in status
    assert "Reflection 上限" not in status
    assert "状态 acting" not in status
    assert "阶段 调用工具" not in status


def test_format_current_action_mentions_latest_tool_call_or_result(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={"tool_calls": [{"id": "call-read", "name": "read_file", "args": {"path": "README.md"}}]},
        )
    )

    assert format_current_action(task) == "准备调用工具 read_file(call-read)"

    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool read_file finished: ok",
            data={"tool_name": "read_file", "tool_call_id": "call-read", "ok": True},
        )
    )

    assert format_current_action(task) == "工具 read_file(call-read) 已成功返回"


def test_format_status_includes_current_action(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.step_count = 1
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={"tool_calls": [{"id": "call-list", "name": "list_files", "args": {"path": "."}}]},
        )
    )
    session.active_task = task

    status = format_status(session)

    assert status == "状态 正在执行 | 当前上下文 0.0%"
    assert "当前 准备调用工具 list_files(call-list)" not in status
    assert "ReAct 上限" not in status
    assert "Reflection 上限" not in status


def test_format_status_shows_context_compression_running(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    started_at = datetime(2026, 7, 9, tzinfo=UTC)
    task.trace_events.append(
        TraceEvent(
            phase="runtime",
            message="Context compression started",
            created_at=started_at,
            data={
                "message_type": "context_compression_started",
                "trigger_usage_percent": 70.0,
                "compression_target": "较早的上下文消息",
                "covered_message_count": 8,
            },
        )
    )
    session.active_task = task

    status = format_status(session, now=started_at + timedelta(seconds=1))

    assert "上下文达到 70.0%" in status
    assert "将对较早的上下文消息进行压缩" in status
    assert "压缩进行中" in status


def test_format_status_keeps_compression_running_for_two_seconds_after_completion(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    started_at = datetime(2026, 7, 9, tzinfo=UTC)
    completed_at = started_at + timedelta(milliseconds=200)
    task.trace_events.extend(
        [
            TraceEvent(
                phase="runtime",
                message="Context compression started",
                created_at=started_at,
                data={
                    "message_type": "context_compression_started",
                    "trigger_usage_percent": 70.0,
                    "compression_target": "较早的上下文消息",
                    "covered_message_count": 8,
                },
            ),
            TraceEvent(
                phase="runtime",
                message="Context compression completed",
                created_at=completed_at,
                data={
                    "message_type": "context_compression_completed",
                    "covered_message_count": 8,
                },
            ),
        ]
    )
    session.active_task = task

    status = format_status(session, now=started_at + timedelta(seconds=1))

    assert "压缩进行中" in status
    assert "上下文已压缩" not in status

    status_after_minimum = format_status(session, now=started_at + timedelta(seconds=3))

    assert "上下文已压缩 8 条" in status_after_minimum


def test_format_status_shows_context_compression_done(tmp_path: Path) -> None:
    from vora.models import CompressionSnapshot

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    session.active_task = task
    session.compression_snapshots.append(
        CompressionSnapshot(
            covered_message_ids=["msg-a", "msg-b"],
            covered_observation_ids=[],
            summary="旧上下文摘要",
            retained_facts=["用户要总结项目"],
        )
    )

    status = format_status(session)

    assert "上下文已压缩" in status
    assert "2 条" in status


def test_format_status_does_not_say_running_after_done_or_failed(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    session.active_task = task

    task.status = "done"
    done_status = format_status(session)
    assert done_status.startswith("状态 已完成")
    assert "正在执行" not in done_status

    task.status = "failed"
    failed_status = format_status(session)
    assert failed_status.startswith("状态 执行失败")
    assert not failed_status.startswith("✔")
    assert "正在执行" not in failed_status


def test_format_artifact_hides_completion_summary(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    task.step_count = 2
    task.result = "报告正文"
    session.active_task = task

    artifact = format_artifact(session)

    assert artifact == "报告正文"
    assert "完成摘要" not in artifact
    assert "状态：planning" not in artifact
    assert "执行步数：2" not in artifact


def test_shift_enter_sequences_are_mapped_to_newline_key() -> None:
    from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
    from prompt_toolkit.keys import Keys

    install_shift_enter_mapping()

    for sequence in SHIFT_ENTER_SEQUENCES:
        assert ANSI_SEQUENCES[sequence] == Keys.ControlJ


def test_insert_newline_inserts_at_cursor() -> None:
    text, cursor = insert_newline("第一行第二行", 3)

    assert text == "第一行\n第二行"
    assert cursor == 4


def test_fresh_tui_does_not_load_persistent_memories(tmp_path: Path) -> None:
    persistent_memory = MemoryManager(tmp_path / ".vora" / "memory.db")
    persistent_memory.add(
        scope="project",
        kind="project_summary",
        content="旧项目优化建议：历史数据不应进入新会话。",
        tags=["project", "summary"],
    )

    tui = PromptTui(cwd=tmp_path)

    assert tui.manager.memory_manager is not None
    assert tui.manager.memory_manager.search("旧项目优化建议") == []


def test_tui_initial_session_uses_project_isolated_memory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    session = SessionState.create(cwd=tmp_path)

    tui = PromptTui(cwd=tmp_path, initial_session=session)

    assert tui.manager.memory_manager is not None
    assert tui.manager.memory_manager.db_path == project_memory_path(tmp_path)


def test_resume_initial_output_shows_full_message_history(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("第一轮问题"))
    session.messages.append(Message.agent("第一轮回答"))
    session.messages.append(Message.user("第二轮问题"))
    task = TaskState.create(goal="第二轮问题", cwd=tmp_path)
    task.status = "done"
    task.result = "第二轮结果"
    session.active_task = task

    tui = PromptTui(cwd=tmp_path, initial_session=session)

    assert "历史概览" in tui.output.text
    assert "第一轮问题" in tui.output.text
    assert "第二轮问题" in tui.output.text
    assert "第二轮结果" in tui.output.text


def test_resume_initial_output_summarizes_long_history_before_task_result(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("第一轮问题"))
    session.messages.append(Message.agent("很长的历史回答\n" + ("细节" * 500)))
    session.messages.append(Message.user("第二轮问题"))
    task = TaskState.create(goal="第二轮问题", cwd=tmp_path)
    task.status = "done"
    task.result = "第二轮结果"
    session.active_task = task

    tui = PromptTui(cwd=tmp_path, initial_session=session)

    assert tui.output.text.index("最近任务") < tui.output.text.index("历史概览")
    assert "第二轮结果" in tui.output.text
    assert "很长的历史回答" not in tui.output.text
    assert "细节" * 200 not in tui.output.text
    assert len(tui.output.text.splitlines()) < 40


def test_resume_initial_output_hides_terminal_task_debug_process(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("修改 skills demo"))
    session.messages.append(Message.agent("执行失败：Reflection pytest gate failed"))
    task = TaskState.create(goal="修改 skills demo", cwd=tmp_path)
    task.status = "failed"
    task.result = "执行失败：测试证据不足"
    task.trace_events.append(
        TraceEvent(
            phase="reflection",
            message="Reflection pytest gate failed",
            data={
                "reason": "pytest_case_path=/tmp/test_reflection_acceptance.py\npytest_case=...",
                "accepted": False,
            },
        )
    )
    session.active_task = task

    tui = PromptTui(cwd=tmp_path, initial_session=session)

    assert "历史概览" in tui.output.text
    assert "最近任务" in tui.output.text
    assert f"Run ID: {task.run_id}" in tui.output.text
    assert "执行失败：测试证据不足" in tui.output.text
    assert "执行过程" not in tui.output.text
    assert "Reflection pytest gate failed" not in tui.output.text
    assert "pytest_case_path" not in tui.output.text


def test_send_current_input_starts_background_turn_without_blocking(monkeypatch) -> None:
    tui = PromptTui()
    started: list[str] = []

    monkeypatch.setattr(tui, "start_agent_turn", started.append)
    tui.input.text = "总结一下项目"

    tui.send_current_input()

    assert started == ["总结一下项目"]
    assert tui.is_running is True
    assert tui.input.text == ""
    assert "用户问题\n总结一下项目" in tui.output.text
    assert "执行过程" in tui.output.text
    assert "• 正在分析请求" in tui.output.text
    assert "暂无工具调用" not in tui.output.text
    assert "步骤概览" not in tui.output.text
    assert tui.status.text.startswith("状态 正在执行")
    assert "running..." not in tui.status.text


def test_send_current_input_outputs_command_directly_without_background_turn(monkeypatch, tmp_path: Path) -> None:
    tui = PromptTui(cwd=tmp_path)
    started: list[str] = []

    monkeypatch.setattr(tui, "start_agent_turn", started.append)
    tui.input.text = "/help"

    tui.send_current_input()

    assert started == []
    assert tui.is_running is False
    assert tui.input.text == ""
    assert "可用指令" in tui.output.text
    assert "常用入口" in tui.output.text
    assert "默认 TUI 入口" in tui.output.text
    assert "一次性任务" in tui.output.text
    assert "MCP 配置" in tui.output.text
    assert "Skills 管理" in tui.output.text
    assert "执行与安全" in tui.output.text
    assert "read_file、write_file、replace_in_file 按用户要求直接执行" in tui.output.text
    assert "运行限制" in tui.output.text
    assert "Enter：发送" in tui.output.text
    assert "/save-context" in tui.output.text
    assert "/compact" in tui.output.text
    assert tui.status.text.startswith("就绪")


def test_status_command_preserves_current_transcript_without_replaying_tool_output(monkeypatch, tmp_path: Path) -> None:
    tui = PromptTui(cwd=tmp_path)
    monkeypatch.setattr(tui, "start_agent_turn", lambda content: None)
    task = TaskState.create(goal="请你看看当前项目还有哪些问题", cwd=tmp_path)
    task.status = "done"
    task.result = "问题清单：暂无阻塞问题。"
    tui.manager.current.active_task = task
    tui.manager.current.messages.extend(
        [
            Message.user("请你看看当前项目还有哪些问题"),
            Message.agent("好的，我先从项目结构开始。"),
            Message.tool("found 46 files", tool_call_id="call-list"),
            Message.agent("继续读取页面。"),
            Message.tool("found 28 files", tool_call_id="call-list-pages"),
        ]
    )
    tui.input.text = "/status"

    tui.send_current_input()

    assert "会话状态" in tui.output.text
    assert "Token usage" in tui.output.text
    assert "用户问题" in tui.output.text
    assert "请你看看当前项目还有哪些问题" in tui.output.text
    assert "最终产物" in tui.output.text
    assert "问题清单：暂无阻塞问题。" in tui.output.text
    assert "当前会话" not in tui.output.text
    assert "found 46 files" not in tui.output.text
    assert "found 28 files" not in tui.output.text


def test_send_current_input_preserves_previous_turn_display(monkeypatch, tmp_path: Path) -> None:
    tui = PromptTui(cwd=tmp_path)
    previous_session = SessionState.create(cwd=tmp_path)
    previous_session.messages.append(Message.user("第一轮"))
    previous_task = TaskState.create(goal="第一轮", cwd=tmp_path)
    previous_task.status = "done"
    previous_task.result = "第一轮结果"
    previous_session.active_task = previous_task
    tui.append_completed_transcript(previous_session)
    monkeypatch.setattr(tui, "start_agent_turn", lambda content: None)

    tui.input.text = "第二轮"
    tui.send_current_input()

    assert "第一轮结果" in tui.output.text
    assert f"Run ID: {previous_task.run_id}" in tui.output.text
    assert "用户问题\n第二轮" in tui.output.text
    assert tui.manager.current.active_task is not None
    assert f"Run ID: {tui.manager.current.active_task.run_id}" in tui.output.text


def test_send_current_input_carries_previous_result_into_context_usage(monkeypatch, tmp_path: Path) -> None:
    tui = PromptTui(cwd=tmp_path)
    tui.manager.current.model_context_limit = 128_000
    previous_task = TaskState.create(goal="第一轮", cwd=tmp_path)
    previous_task.status = "done"
    previous_task.result = "上一轮结果" + ("x" * 1000)
    previous_task.limits.max_estimated_tokens = 10_000
    tui.manager.current.messages.append(Message.user("第一轮"))
    tui.manager.current.active_task = previous_task
    tui.append_completed_transcript(tui.manager.current)
    monkeypatch.setattr(tui, "start_agent_turn", lambda content: None)

    tui.input.text = "第二轮"
    tui.send_current_input()

    assert any(message.role == "system" and "上一轮结果" in message.content for message in tui.manager.current.messages)
    assert tui.status.text == "状态 正在执行 | 当前上下文 0.4% | 上下文窗口上限 128,000"


def test_send_current_input_hides_previous_compression_status(monkeypatch, tmp_path: Path) -> None:
    from vora.models import CompressionSnapshot

    tui = PromptTui(cwd=tmp_path)
    tui.manager.current.compression_snapshots.append(
        CompressionSnapshot(
            covered_message_ids=["msg-a", "msg-b"],
            covered_observation_ids=[],
            summary="上一轮压缩摘要",
        )
    )
    previous_task = TaskState.create(goal="第一轮", cwd=tmp_path)
    previous_task.status = "done"
    previous_task.result = "第一轮结果"
    tui.manager.current.active_task = previous_task
    monkeypatch.setattr(tui, "start_agent_turn", lambda content: None)

    tui.input.text = "第二轮"
    tui.send_current_input()

    assert "上下文已压缩" not in tui.status.text
    assert tui.manager.current.active_task is not None
    assert tui.manager.current.active_task.metadata["compression_snapshot_start_index"] == 1


def test_send_current_input_uses_session_context_limit_before_background_turn(monkeypatch, tmp_path: Path) -> None:
    tui = PromptTui(cwd=tmp_path)
    tui.manager.current.model_context_limit = 1_000_000
    previous_task = TaskState.create(goal="第一轮", cwd=tmp_path)
    previous_task.status = "done"
    previous_task.result = "x" * 64_000
    tui.manager.current.active_task = previous_task
    monkeypatch.setattr(tui, "start_agent_turn", lambda content: None)

    tui.input.text = "第二轮"
    tui.send_current_input()

    assert tui.manager.current.active_task is not None
    assert tui.manager.current.active_task.model_context_limit == 1_000_000
    assert tui.status.text == "状态 正在执行 | 当前上下文 3.2% | 上下文窗口上限 1,000,000"


def test_resume_stream_session_preserves_existing_history(tmp_path: Path) -> None:
    async def run() -> None:
        session = SessionState.create(cwd=tmp_path)
        session.messages.append(Message.user("第一轮"))
        first_task = TaskState.create(goal="第一轮", cwd=tmp_path)
        first_task.status = "done"
        first_task.result = "第一轮结果"
        session.active_task = first_task
        tui = PromptTui(cwd=tmp_path, initial_session=session)

        session.messages.append(Message.user("第二轮"))
        second_task = TaskState.create(goal="第二轮", cwd=tmp_path)
        second_task.status = "done"
        second_task.result = "第二轮结果"
        session.active_task = second_task

        await tui.stream_session(session)

        assert "第一轮结果" in tui.output.text
        assert f"Run ID: {first_task.run_id}" in tui.output.text
        assert "第二轮结果" in tui.output.text
        assert f"Run ID: {second_task.run_id}" in tui.output.text

    asyncio.run(run())


def test_run_agent_turn_resets_state_after_exception(monkeypatch) -> None:
    class FailingManager:
        current = SessionState.create(cwd=Path("."))

        def handle_user_message(self, content: str, append_user_message: bool = True):  # noqa: ARG002
            raise RuntimeError("boom")

    async def run() -> None:
        tui = PromptTui()
        tui.manager = FailingManager()
        tui.is_running = True
        tui.output.text = "你: 测试"

        await tui.run_agent_turn("测试")

        assert tui.is_running is False
        assert "执行失败：boom" in tui.output.text
        assert "最终产物" in tui.output.text
        assert tui.status.text.startswith("failed")

    asyncio.run(run())


def test_run_agent_turn_saves_unexpected_error_for_resume(tmp_path: Path) -> None:
    class FailingManager:
        def __init__(self) -> None:
            self.current = SessionState.create(cwd=tmp_path)
            self.current.active_task = TaskState.create(goal="测试", cwd=tmp_path)
            self.saved = []

        def handle_user_message(self, content: str, append_user_message: bool = True):  # noqa: ARG002
            raise RuntimeError("boom")

        def _save_current(self, session):  # noqa: ANN001
            self.saved.append(session.model_copy(deep=True))
            return session

    async def run() -> None:
        manager = FailingManager()
        tui = PromptTui(cwd=tmp_path)
        tui.manager = manager
        tui.is_running = True

        await tui.run_agent_turn("测试")

        assert manager.current.active_task is not None
        assert manager.current.active_task.status == "failed"
        assert manager.current.active_task.errors[-1].code == "UNKNOWN_ERROR"
        assert manager.current.active_task.result == "执行失败：boom"
        assert manager.current.messages[-1].content == "执行失败：boom"
        assert manager.saved

    asyncio.run(run())


def test_handle_interrupted_execution_marks_failed_and_completes_tool_messages(tmp_path: Path) -> None:
    from vora.context import validate_tool_call_pairs

    tui = PromptTui(cwd=tmp_path)
    task = TaskState.create(goal="读取文件", cwd=tmp_path)
    tui.manager.current.active_task = task
    tui.manager.current.messages.append(Message.user("读取文件"))
    tui.manager.current.messages.append(Message.agent("需要读取", tool_call_ids=["call-read"]))
    tui.is_running = True

    tui.handle_interrupted_execution()

    assert tui.is_running is False
    assert tui.manager.current.active_task is not None
    assert tui.manager.current.active_task.status == "failed"
    assert tui.status.text == "状态 执行失败 | 当前上下文 0.0%"
    validate_tool_call_pairs(tui.manager.current.messages[:-1])
    assert any(
        message.role == "tool"
        and message.tool_call_id == "call-read"
        and "USER_CANCELLED" in message.content
        for message in tui.manager.current.messages
    )


def test_request_exit_while_running_detaches_background_executor(tmp_path: Path) -> None:
    class FakeApp:
        def __init__(self) -> None:
            self.exited = False

        def exit(self) -> None:
            self.exited = True

    tui = PromptTui(cwd=tmp_path)
    task = TaskState.create(goal="长任务", cwd=tmp_path)
    tui.manager.current.active_task = task
    tui.is_running = True
    fake_app = FakeApp()

    tui.request_exit(fake_app)  # type: ignore[arg-type]
    tui.request_exit(fake_app)  # type: ignore[arg-type]

    assert fake_app.exited is True
    assert tui.is_running is False
    assert tui._agent_executor_shutdown is True
    assert tui.manager.current.active_task is not None
    assert tui.manager.current.active_task.status == "failed"


def test_render_progress_prints_trace_while_running(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    tui = PromptTui(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={"tool_calls": [{"name": "read_file", "args": {"path": "README.md"}}]},
        )
    )
    tui.manager.current.active_task = task

    tui.render_progress()

    assert "LLM 回合" not in tui.output.text
    assert "工具调度" not in tui.output.text
    assert "● Ran 1 read_file README.md" in tui.output.text
    assert "  └ waiting" in tui.output.text
    assert "最近过程（折叠）" not in tui.output.text
    assert "返回预览" not in tui.output.text


def test_render_progress_reveals_trace_events_incrementally(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    tui = PromptTui(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    for index in range(6):
        task.trace_events.append(
            TraceEvent(
                phase="react",
                message=f"event {index}",
                data={"index": index},
            )
        )
    tui.manager.current.active_task = task
    tui.visible_trace_count = 0

    tui.render_progress()

    assert tui.visible_trace_count == 3
    assert "最近过程（折叠）" not in tui.output.text

    tui.render_progress()

    assert tui.visible_trace_count == 6
    assert "最近过程（折叠）" not in tui.output.text


def test_render_progress_keeps_output_scrolled_to_latest_content(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    tui = PromptTui(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    for index in range(30):
        task.trace_events.append(
            TraceEvent(
                phase="tool",
                message=f"Tool read_file finished: ok {index}",
                data={"line": index},
            )
        )
    tui.manager.current.active_task = task

    tui.render_progress()

    assert tui.output.buffer.cursor_position == len(tui.output.text)


def test_output_area_is_focusable_and_input_stays_compact() -> None:
    tui = PromptTui()

    assert tui.output.control.focusable() is True
    assert tui.input.window.height.preferred == 3
    assert tui.input.window.height.max == 4


def test_prompt_tui_initial_output_shows_welcome_instead_of_empty_artifact() -> None:
    tui = PromptTui()

    assert "欢迎使用 Vora" in tui.output.text
    assert "直接输入任务开始连续对话" in tui.output.text
    assert "输入 `/help` 查看常用入口" in tui.output.text
    assert "工程循环上限" not in tui.output.text
    assert "当前产物会显示在这里" not in tui.output.text


def test_set_output_text_preserves_manual_scroll_position_when_not_following_bottom() -> None:
    tui = PromptTui()
    tui.set_output_text("\n".join(f"line {index}" for index in range(80)))
    tui.output.buffer.cursor_position = 0

    tui.set_output_text("\n".join(f"updated line {index}" for index in range(80)))

    assert tui.output.buffer.cursor_position == 0


def test_output_scroll_can_move_between_start_middle_and_end() -> None:
    tui = PromptTui()
    text = "\n".join(f"line {index}" for index in range(120))
    tui.set_output_text(text, force_follow=True)

    assert tui.output.buffer.cursor_position == len(tui.output.text)

    tui.scroll_output_to_start()
    assert tui.output.buffer.cursor_position == 0

    tui.scroll_output(10)
    assert tui.output.document.cursor_position_row == 10

    tui.scroll_output_to_end()
    assert tui.output.buffer.cursor_position == len(tui.output.text)


def test_output_scroll_page_size_reaches_full_history_faster() -> None:
    tui = PromptTui()
    text = "\n".join(f"line {index}" for index in range(300))
    tui.set_output_text(text, force_follow=True)

    tui.scroll_output(-60)

    assert tui.output.document.cursor_position_row <= 240


def test_line_start_index_maps_positions_without_resplitting_text() -> None:
    text = "line 0\nline 1\nline 2"
    line_starts = build_line_starts(text)

    assert line_starts == [0, 7, 14]
    assert line_number_for_position(line_starts, 0) == 0
    assert line_number_for_position(line_starts, 8) == 1
    assert line_number_for_position(line_starts, len(text)) == 2


def test_display_line_starts_include_soft_wrapped_rows() -> None:
    assert build_display_line_starts("abcdefghij", width=4) == [0, 4, 8]
    assert build_display_line_starts("abcd\nefghij", width=4) == [0, 5, 9]


def test_output_scroll_uses_soft_wrapped_display_lines() -> None:
    tui = PromptTui()
    text = "x" * 400
    tui.output.get_display_width = lambda: 40  # type: ignore[method-assign]
    tui.output.get_visible_height = lambda: 1  # type: ignore[method-assign]
    tui.set_output_text(text, force_follow=True)

    tui.scroll_output(-5)

    assert tui.output.scroll_top == 4
    assert tui.output.buffer.cursor_position == 160

    tui.scroll_output(-5)

    assert tui.output.scroll_top == 0
    assert tui.output.buffer.cursor_position == 0


def test_output_view_can_scroll_freely_from_bottom_to_top() -> None:
    tui = PromptTui()
    tui.output.get_display_width = lambda: 20  # type: ignore[method-assign]
    tui.output.get_visible_height = lambda: 5  # type: ignore[method-assign]
    tui.set_output_text("x" * 2_000, force_follow=True)

    assert tui.output.scroll_top == tui.output.max_scroll_top()

    for _ in range(100):
        tui.scroll_output(-5)

    assert tui.output.scroll_top == 0
    assert tui.output.buffer.cursor_position == 0

    for _ in range(100):
        tui.scroll_output(5)

    assert tui.output.scroll_top == tui.output.max_scroll_top()
    assert tui.output.buffer.cursor_position == len(tui.output.text)


def test_output_view_handles_mouse_wheel_events() -> None:
    tui = PromptTui()
    tui.output.get_display_width = lambda: 20  # type: ignore[method-assign]
    tui.output.get_visible_height = lambda: 5  # type: ignore[method-assign]
    tui.set_output_text("x" * 2_000, force_follow=True)
    start = tui.output.scroll_top

    tui.output.control.mouse_handler(
        MouseEvent(
            position=Point(x=10, y=3),
            event_type=MouseEventType.SCROLL_UP,
            button=MouseButton.NONE,
            modifiers=frozenset(),
        )
    )

    assert tui.output.scroll_top == start - 5

    tui.output.control.mouse_handler(
        MouseEvent(
            position=Point(x=10, y=3),
            event_type=MouseEventType.SCROLL_DOWN,
            button=MouseButton.NONE,
            modifiers=frozenset(),
        )
    )

    assert tui.output.scroll_top == start


def test_output_scroll_uses_cached_line_index_for_large_content() -> None:
    tui = PromptTui()
    text = "\n".join(f"line {index}" for index in range(20_000))
    tui.set_output_text(text, force_follow=True)
    original_index = tui.output_line_starts

    tui.scroll_output_to_start()
    for _ in range(100):
        tui.scroll_output(1)

    assert tui.output.document.cursor_position_row == 100
    assert tui.output_line_starts is original_index


def test_render_progress_does_not_rewrite_output_while_user_is_reading_history(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    tui = PromptTui(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    for index in range(40):
        task.trace_events.append(
            TraceEvent(
                phase="llm",
                message="LLM requested 1 tool call(s)",
                data={
                    "iteration": index + 1,
                    "reasoning_content": f"第 {index} 轮需要继续检查上下文并读取更多文件。" + ("补充说明 " * 12),
                    "tool_calls": [
                        {
                            "id": f"call-read-{index}",
                            "name": "read_file",
                            "args": {"path": f"docs/file-{index}.md"},
                        }
                    ],
                },
            )
        )
    tui.manager.current.active_task = task
    tui.render_progress()
    tui.scroll_output_to_start()
    visible_before = tui.visible_trace_count
    output_before = tui.output.text

    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 1 tool call(s)",
            data={
                "iteration": 41,
                "reasoning_content": "新增一轮执行过程。",
                "tool_calls": [{"id": "call-read-40", "name": "read_file", "args": {"path": "docs/file-40.md"}}],
            },
        )
    )
    tui.render_progress()

    assert tui.visible_trace_count == visible_before
    assert tui.output.text == output_before
    assert tui.status.text == "状态 正在执行 | 当前上下文 0.0%"


def test_stream_session_keeps_tui_busy_until_artifact_stream_finishes(tmp_path: Path) -> None:
    async def run() -> None:
        tui = PromptTui(cwd=tmp_path)
        session = SessionState.create(cwd=tmp_path)
        task = TaskState.create(goal="写报告", cwd=tmp_path)
        task.result = "这是一段足够长的产物内容，用来覆盖流式输出期间的运行状态。"
        session.active_task = task
        tui.is_running = True

        stream_task = asyncio.create_task(tui.stream_session(session))
        await asyncio.sleep(0)

        assert tui.is_running is True

        await stream_task
        assert tui.is_running is False
        assert tui.status.text.startswith("状态 已结束")

    asyncio.run(run())


def test_stream_session_status_shows_done_when_task_is_done(tmp_path: Path) -> None:
    async def run() -> None:
        tui = PromptTui(cwd=tmp_path)
        session = SessionState.create(cwd=tmp_path)
        task = TaskState.create(goal="写报告", cwd=tmp_path)
        task.status = "done"
        task.result = "报告正文"
        session.active_task = task
        tui.is_running = True

        await tui.stream_session(session)

        assert tui.status.text.startswith("状态 已完成")

    asyncio.run(run())


def test_stream_session_keeps_final_artifact_structure_during_stream(tmp_path: Path) -> None:
    async def run() -> None:
        tui = PromptTui(cwd=tmp_path)
        session = SessionState.create(cwd=tmp_path)
        session.messages.append(Message.user("写报告"))
        task = TaskState.create(goal="写报告", cwd=tmp_path)
        task.status = "done"
        task.step_count = 2
        task.result = "报告正文需要流式展示出来。"
        session.active_task = task

        stream_task = asyncio.create_task(tui.stream_session(session))
        await asyncio.sleep(0.04)

        assert "最终产物" in tui.output.text
        assert "完成摘要" not in tui.output.text
        assert "结果正文" not in tui.output.text
        assert "执行过程" in tui.output.text

        await stream_task

    asyncio.run(run())


def test_stream_session_preserves_scroll_when_reading_and_resumes_following_at_bottom(tmp_path: Path) -> None:
    async def run() -> None:
        from vora.models import TraceEvent

        tui = PromptTui(cwd=tmp_path)
        session = SessionState.create(cwd=tmp_path)
        session.messages.append(Message.user("写报告"))
        task = TaskState.create(goal="写报告", cwd=tmp_path)
        task.status = "done"
        task.result = "报告正文-" + ("流式内容" * 80)
        for index in range(12):
            task.trace_events.append(
                TraceEvent(
                    phase="llm",
                    message="LLM requested 1 tool call(s)",
                    data={
                        "iteration": index + 1,
                        "reasoning_content": f"第 {index} 轮整理报告上下文。" + ("补充说明 " * 12),
                        "tool_calls": [
                            {
                                "id": f"call-read-{index}",
                                "name": "read_file",
                                "args": {"path": f"docs/file-{index}.md"},
                            }
                        ],
                    },
                )
            )
        session.active_task = task
        tui.is_running = True

        stream_task = asyncio.create_task(tui.stream_session(session))
        await asyncio.sleep(0.04)

        tui.scroll_output_to_start()
        text_while_reading = tui.output.text
        cursor_while_reading = tui.output.buffer.cursor_position
        await asyncio.sleep(0.08)

        assert tui.output.buffer.cursor_position == cursor_while_reading
        assert len(tui.output.text) > len(text_while_reading)
        assert tui.output.buffer.cursor_position < len(tui.output.text)

        tui.scroll_output_to_end()
        await asyncio.sleep(0.08)

        assert tui.output.buffer.cursor_position == len(tui.output.text)

        await stream_task
        assert tui.output.buffer.cursor_position == len(tui.output.text)

    asyncio.run(run())


def test_stream_session_final_output_can_scroll_back_to_full_history(tmp_path: Path) -> None:
    from vora.models import TraceEvent

    async def run() -> None:
        tui = PromptTui(cwd=tmp_path)
        session = SessionState.create(cwd=tmp_path)
        session.messages.append(Message.user("写报告"))
        task = TaskState.create(goal="写报告", cwd=tmp_path)
        task.status = "done"
        task.result = "最终报告"
        for index in range(80):
            task.trace_events.append(
                TraceEvent(
                    phase="tool",
                    message=f"Tool read_file finished: ok {index}",
                    data={
                        "tool_name": "read_file",
                        "tool_call_id": f"call-{index}",
                        "ok": True,
                        "summary": f"read file {index}",
                    },
                )
            )
        session.active_task = task

        await tui.stream_session(session)

        assert "执行过程" in tui.output.text
        assert "最终产物" in tui.output.text
        assert len(tui.output_line_starts) > 30

        tui.scroll_output_to_start()

        assert tui.output.buffer.cursor_position == 0
        assert tui.output.document.cursor_position_row == 0
        assert "会话信息" in tui.output.text.splitlines()[1]
        assert "用户问题" in tui.output.text

    asyncio.run(run())


def test_run_agent_turn_handles_session_without_active_task(tmp_path: Path) -> None:
    class EmptyTaskManager:
        current = SessionState.create(cwd=tmp_path)

        def handle_user_message(self, content: str, append_user_message: bool = True):  # noqa: ARG002
            return self.current

    async def run() -> None:
        tui = PromptTui(cwd=tmp_path)
        tui.manager = EmptyTaskManager()
        tui.is_running = True

        await tui.run_agent_turn("测试")

        assert tui.is_running is False
        assert "执行失败" in tui.output.text
        assert tui.status.text.startswith("failed")

    asyncio.run(run())
