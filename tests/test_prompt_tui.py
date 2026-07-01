import asyncio
from pathlib import Path

from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from manus_mini.models import Message, Observation, SessionState, TaskState
from manus_mini.prompt_tui import (
    SHIFT_ENTER_SEQUENCES,
    PromptTui,
    build_display_line_starts,
    build_line_starts,
    format_artifact,
    format_current_action,
    format_inline_args,
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
)


def test_format_messages_renders_chinese_user_content(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("帮我写一份 AI 调研报告。"))
    session.messages.append(Message.agent("可以，我先整理结构。"))

    rendered = format_messages(session)

    assert "│  帮我写一份 AI 调研报告。" in rendered
    assert "Agent: 可以，我先整理结构。" in rendered


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
    assert "外层工程循环上限：12 轮" in welcome
    assert "ReAct 上限：8 轮" in welcome
    assert "Reflection 上限：5 轮" in welcome
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

    assert "工具返回：read_file(unknown) 已返回" in process
    assert "INVALID_TOOL_PARAMS" in process


def test_format_process_groups_current_step_tool_calls_and_observations(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.step_count = 2
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
    assert "当前动作" in process
    assert "工具调用" in process
    assert "read_file(call-read)" in process
    assert "path: README.md" in process
    assert "path='README.md'" not in process
    assert "工具返回" in process
    assert "read README.md" in process
    assert "# demo project" in process


def test_format_process_summarizes_trace_without_raw_nested_json(tmp_path: Path) -> None:
    from manus_mini.models import TraceEvent

    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    task.trace_events.append(
        TraceEvent(
            phase="llm",
            message="LLM requested 2 tool call(s)",
            data={
                "iteration": 1,
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
                "iteration": 1,
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

    assert "准备调用：list_files(call-list), read_file(call-read)" in process
    assert "工具返回：read_file(call-read) 成功，read README.md" in process
    assert '"tool_calls"' not in process
    assert "{'tool_calls'" not in process
    assert "[{" not in process


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
    assert "…" in process


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
    assert "# demo project" in process


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

    process = format_process(session, max_events=5)

    assert "仅展示最近 5 条" in process
    assert "event 0" not in process
    assert "event 7" in process
    assert "event 11" in process


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
    assert "[REDACTED]" in process


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
    assert "对话记录" in transcript
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

    assert "对话" in transcript
    assert "执行过程" in transcript
    assert "产物" in transcript
    assert "最终总结" in transcript
    assert transcript.count("最终总结") == 1
    assert "工具：" in transcript


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
    assert "阶段 调用工具" in status


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

    assert "阶段 规划任务" in status
    assert "当前 准备调用工具 list_files(call-list)" in status
    assert "ReAct 上限" not in status
    assert "Reflection 上限" not in status


def test_format_status_does_not_say_running_after_done_or_failed(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="总结项目", cwd=tmp_path)
    session.active_task = task

    task.status = "done"
    done_status = format_status(session)
    assert done_status.startswith("已完成")
    assert "正在执行" not in done_status

    task.status = "failed"
    failed_status = format_status(session)
    assert failed_status.startswith("执行失败")
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


def test_send_current_input_starts_background_turn_without_blocking(monkeypatch) -> None:
    tui = PromptTui()
    started: list[str] = []

    monkeypatch.setattr(tui, "start_agent_turn", started.append)
    tui.input.text = "总结一下项目"

    tui.send_current_input()

    assert started == ["总结一下项目"]
    assert tui.is_running is True
    assert tui.input.text == ""
    assert "│  总结一下项目" in tui.output.text
    assert "执行过程" in tui.output.text
    assert "当前步骤" in tui.output.text
    assert tui.status.text.startswith("正在执行")
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

    assert "LLM：准备调用：read_file(unknown)" in tui.output.text
    assert "read_file" in tui.output.text


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

    assert "event 0" in tui.output.text
    assert "event 2" in tui.output.text
    assert "event 3" not in tui.output.text
    assert tui.visible_trace_count == 3

    tui.render_progress()

    assert "event 5" in tui.output.text
    assert tui.visible_trace_count == 6


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
    task.trace_events.append(TraceEvent(phase="react", message="event 0"))
    tui.manager.current.active_task = task
    tui.render_progress()
    tui.scroll_output_to_start()
    visible_before = tui.visible_trace_count
    output_before = tui.output.text

    task.trace_events.append(TraceEvent(phase="react", message="event 1"))
    tui.render_progress()

    assert tui.visible_trace_count == visible_before
    assert tui.output.text == output_before


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
        assert tui.status.text.startswith("已结束")

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

        assert tui.status.text.startswith("已完成")

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
