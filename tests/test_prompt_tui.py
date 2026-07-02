import asyncio
from pathlib import Path

from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from manus_mini.memory import MemoryManager
from manus_mini.logging import project_memory_path
from manus_mini.models import Message, Observation, PlanStep, SessionState, TaskState, TraceEvent
from manus_mini.prompt_tui import (
    SHIFT_ENTER_SEQUENCES,
    PromptTui,
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
            "read README.md\n\ncontent:\n# manus-mini\n\nmore text",
            tool_call_id="call-read",
        )
    )

    rendered = format_messages(session)

    assert "工具: [README.md 文件内容获取成功]" in rendered
    assert "# manus-mini" not in rendered
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
    welcome = format_welcome(task.limits)

    assert "欢迎使用 Manus Mini" in welcome
    assert "外层工程循环上限：3 轮" in welcome
    assert "ReAct 上限：20 轮" in welcome
    assert "Reflection 上限：3 轮" in welcome
    assert "单轮运行超时" not in welcome
    assert "压缩上下文" in welcome
    assert "/compact" in welcome
    assert "/save-context" in welcome
    assert "/help" in welcome
    assert "Enter 发送" in welcome
    assert "Shift+Enter 换行" in welcome


def test_format_artifact_renders_active_task_result(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    task.result = "报告正文"
    session.active_task = task

    artifact = format_artifact(session)
    assert "完成摘要" in artifact
    assert "结果正文" in artifact
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
            "────────────────────────────────────────\n执行过程\n当前步骤\n- 调用工具",
            "────────────────────────────────────────\n最终产物\n结果正文\n报告",
        ]
    )

    fragments = style_output_fragments(text)
    process_fragment = next(fragment for fragment in fragments if "当前步骤" in fragment[1])
    artifact_fragment = next(fragment for fragment in fragments if "报告" in fragment[1])

    assert "class:process" in process_fragment[0]
    assert "class:process" not in artifact_fragment[0]


def test_output_fragments_color_diff_additions_and_removals_in_process_section() -> None:
    text = "\n\n".join(
        [
            "────────────────────────────────────────\n执行过程\nLLM 回合 1\n- 1 replace_in_file(call) 变更预览:\n  --- a/app.py\n  +++ b/app.py\n  @@ -1 +1 @@\n  -old\n  +new\n  context",
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
        "LLM 回合 1\n"
        "工具调度\n"
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
    from manus_mini.models import TraceEvent

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
    assert "工具返回" in process
    assert "read_file(unknown)" in process
    assert "Tool read_file finished: failed" in process
    assert "最近过程（折叠）" not in process


def test_format_context_usage_counts_only_messages(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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

    assert usage == "上下文 5%"


def test_format_context_usage_prefers_llm_usage_when_available(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="统计上下文", cwd=tmp_path)
    task.model_context_limit = 1_000
    task.last_prompt_tokens = 250
    task.limits.max_estimated_tokens = 100
    session.messages.append(Message.user("x" * 10))
    session.active_task = task

    usage = format_context_usage(session)

    assert usage == "上下文 25.0%"


def test_latest_activity_formats_latest_event_for_status_bar(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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
    assert status == "状态 正在执行 | Enter 发送 | Shift+Enter 换行"


def test_format_process_groups_current_step_tool_calls_and_observations(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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

    assert "当前步骤" in process
    assert "第 2 步" in process
    assert "执行计划" in process
    assert "[已完成] 扫描工作目录并识别项目结构" in process
    assert "[进行中] 读取关键文档" in process
    assert "当前动作" in process
    assert "工具调度" in process
    assert "共 1 个批次" in process
    assert "第 1 批（1 个工具）" in process
    assert "1.1 调用 read_file(call-read) path: README.md" in process
    assert "1.1 read_file(call-read) 已返回: read README.md" in process
    assert "# demo project" not in process
    assert "最近过程（折叠）" not in process


def test_format_process_orders_llm_content_before_matching_tool_call_and_result(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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

    llm_index = process.index("LLM 回合")
    schedule_index = process.index("工具调度")
    batch_index = process.index("第 1 批（1 个工具）")
    assert llm_index < schedule_index < batch_index
    assert "我需要先确认 README 内容。" not in process
    assert "已返回" in process
    assert "1.1 调用 read_file(call-read) path: README.md" in process
    assert "1.1 read_file(call-read) 成功: read README.md" in process


def test_format_process_summarizes_trace_without_raw_nested_json(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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

    assert "LLM 回合 10" in process
    assert "工具调度" in process
    assert "共 1 个批次" in process
    assert "第 1 批（2 个工具）" in process
    assert "10.1 调用 list_files(call-list) path: ." in process
    assert "10.2 调用 read_file(call-read) path: README.md" in process
    assert "10.2 read_file(call-read) 成功: read README.md" in process
    assert '"tool_calls"' not in process
    assert "{'tool_calls'" not in process
    assert "[{" not in process


def test_format_process_groups_tool_returns_by_planned_batch(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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

    assert "LLM 回合 10" in process
    assert "共 2 个批次" in process
    assert "第 1 批（2 个工具）" in process
    assert "第 2 批（1 个工具）" in process
    assert "10.1 调用 list_files(call-list) path: ." in process
    assert "10.2 调用 read_file(call-read) path: README.md" in process
    assert "10.3 调用 read_file(call-docs) path: docs/design.md" in process
    assert "10.1 list_files(call-list) 成功: found 3 files" in process
    assert "10.2 read_file(call-read) 成功: read README.md" in process
    assert "10.3 read_file(call-docs) 成功: read docs/design.md" in process


def test_format_process_shows_llm_returned_content(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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

    assert "LLM 返回" in process
    assert "我需要先确认项目结构，所以准备读取文件列表。" not in process
    assert "返回内容" not in process


def test_format_tool_return_without_ok_flag_uses_neutral_status(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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

    assert "read_file(call-read) 已返回" in process
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
    from manus_mini.models import TraceEvent

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

    assert status == "状态 正在执行 | Enter 发送 | Shift+Enter 换行"


def test_format_process_highlights_phase_and_current_action(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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

    assert "阶段：调用工具" in process
    assert "当前动作：准备调用工具 read_file(call-read)" in process


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

    assert "工具返回" in process
    assert "call-read" in process
    assert "read README.md" in process
    assert "# demo project" not in process


def test_format_process_limits_old_events_but_keeps_current_state(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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

    assert "event 11" in process
    assert "最近过程（折叠）" not in process
    assert "已折叠" not in process


def test_format_process_redacts_sensitive_trace_data(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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
    assert "LLM 回合" in process
    assert "最近过程（折叠）" not in process


def test_format_transcript_shows_process_while_running(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("总结项目"))
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(TraceEvent(phase="tool", message="Tool list_files finished: ok"))
    session.active_task = task

    transcript = format_transcript(session, show_process=True)

    assert "────────────────" in transcript
    assert "用户问题" in transcript
    assert "总结项目" in transcript
    assert "对话记录" not in transcript
    assert "执行过程" in transcript
    assert "当前步骤" in transcript
    assert "Tool list_files finished: ok" in transcript
    assert "产物" not in transcript


def test_format_transcript_keeps_process_for_final_artifact(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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
    from manus_mini.models import TraceEvent

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


def test_format_status_keeps_send_hint(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)

    status = format_status(session)

    assert "Enter 发送" in status
    assert "Shift+Enter 换行" in status


def test_format_status_shows_context_usage(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.limits.max_estimated_tokens = 100
    session.active_task = task
    session.messages.append(Message.user("x" * 50))

    status = format_status(session)

    assert "上下文 25%" not in status
    assert format_context_usage(session) == "上下文 25%"


def test_prompt_tui_renders_confirmation_overlay(tmp_path: Path) -> None:
    from manus_mini.models import PendingConfirmation

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
    from manus_mini.models import PendingConfirmation

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
    from manus_mini.models import PendingConfirmation

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
    from manus_mini.models import PendingConfirmation

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
    from manus_mini.models import PendingConfirmation

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


def test_format_status_context_usage_includes_active_task_process(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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

    assert format_context_usage(session) == "上下文 0%"


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
    from manus_mini.models import TraceEvent

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
    from manus_mini.models import TraceEvent

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

    assert status == "状态 正在执行 | Enter 发送 | Shift+Enter 换行"
    assert "当前 准备调用工具 list_files(call-list)" not in status
    assert "ReAct 上限" not in status
    assert "Reflection 上限" not in status


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


def test_format_artifact_adds_completion_summary(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    task.step_count = 2
    task.result = "报告正文"
    session.active_task = task

    artifact = format_artifact(session)

    assert artifact.startswith("完成摘要\n")
    assert "状态：planning" in artifact
    assert "执行步数：2" in artifact
    assert "报告正文" in artifact


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
    persistent_memory = MemoryManager(tmp_path / ".manus-mini" / "memory.db")
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
    assert "当前步骤" in tui.output.text
    assert tui.status.text.startswith("状态 正在执行")
    assert "running..." not in tui.status.text


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


def test_render_progress_prints_trace_while_running(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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

    assert "LLM 回合" in tui.output.text
    assert "工具调度" in tui.output.text
    assert "最近过程（折叠）" not in tui.output.text
    assert "返回预览" not in tui.output.text


def test_render_progress_reveals_trace_events_incrementally(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

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
    from manus_mini.models import TraceEvent

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

    assert "欢迎使用 Manus Mini" in tui.output.text
    assert "外层工程循环上限" in tui.output.text
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
    from manus_mini.models import TraceEvent

    tui = PromptTui(cwd=tmp_path)
    task = TaskState.create(goal="写报告", cwd=tmp_path)
    task.plan = [
        PlanStep(description=f"步骤 {index}：收集和整理项目信息", intent="research", status="pending")
        for index in range(80)
    ]
    for index in range(40):
        task.trace_events.append(TraceEvent(phase="react", message=f"event {index}"))
    tui.manager.current.active_task = task
    tui.render_progress()
    tui.scroll_output_to_start()
    visible_before = tui.visible_trace_count
    output_before = tui.output.text

    task.trace_events.append(TraceEvent(phase="react", message="event 40"))
    tui.render_progress()

    assert tui.visible_trace_count == visible_before
    assert tui.output.text == output_before
    assert tui.status.text == "状态 正在执行 | Enter 发送 | Shift+Enter 换行"


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
        assert "完成摘要" in tui.output.text
        assert "结果正文" in tui.output.text
        assert "执行过程" in tui.output.text

        await stream_task

    asyncio.run(run())


def test_stream_session_preserves_scroll_when_reading_and_resumes_following_at_bottom(tmp_path: Path) -> None:
    async def run() -> None:
        tui = PromptTui(cwd=tmp_path)
        session = SessionState.create(cwd=tmp_path)
        session.messages.append(Message.user("写报告"))
        task = TaskState.create(goal="写报告", cwd=tmp_path)
        task.status = "done"
        task.result = "报告正文-" + ("流式内容" * 80)
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
    from manus_mini.models import TraceEvent

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
        assert "用户问题" in tui.output.text.splitlines()[1]

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
