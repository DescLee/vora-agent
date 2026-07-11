from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import ConditionalContainer, Float, FloatContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.screen import Point
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, Label, TextArea

from vora.config import AppConfig
from vora.executor import _detach_threads_from_python_shutdown
from vora.logging import project_memory_path
from vora.models import AgentError, LoopLimits, Message, PendingConfirmation, SessionState, TaskState
from vora.memory import MemoryManager
from vora.prompt_tui_formatting import (  # noqa: F401
    build_display_line_starts,
    build_line_starts,
    format_artifact,
    format_context_usage,
    format_current_action,
    format_display_value,
    format_event_details,
    format_event_summary,
    format_inline_args,
    format_latest_activity,
    format_messages,
    format_message_block,
    format_section,
    format_phase_label,
    format_process,
    format_status,
    format_status_label,
    format_tool_return_status,
    format_trace_data,
    format_transcript,
    format_welcome,
    insert_newline,
    line_number_for_position,
    render_markdown_result,
    style_confirmation_fragments,
    style_output_fragments,
    wrap_text_for_display,
)
from vora.session import COMPACT_CONTEXT_COMMANDS, HELP_COMMANDS, SAVE_CONTEXT_COMMANDS, SessionManager


@dataclass(slots=True)
class PromptTuiOptions:
    cwd: Path
    limits: LoopLimits
    dry_run: bool = False

SHIFT_ENTER_SEQUENCES = (
    "\x1b[27;2;13~",
    "\x1b[13;2u",
)
AGENT_THREAD_NAME_PREFIX = "vora-agent"
RESUME_HISTORY_MESSAGE_LIMIT = 8
RESUME_HISTORY_PREVIEW_CHARS = 180
RESUME_RESULT_PREVIEW_CHARS = 900


def install_shift_enter_mapping() -> None:
    from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
    from prompt_toolkit.keys import Keys

    for sequence in SHIFT_ENTER_SEQUENCES:
        ANSI_SEQUENCES[sequence] = Keys.ControlJ


def _short_resume_text(content: str, limit: int) -> str:
    compact = " ".join(line.strip() for line in content.splitlines() if line.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


class ScrollPositionBuffer:
    def __init__(self, view: "ScrollableOutputView") -> None:
        self.view = view

    @property
    def cursor_position(self) -> int:
        return self.view.cursor_position

    @cursor_position.setter
    def cursor_position(self, value: int) -> None:
        self.view.cursor_position = value


class ScrollPositionDocument:
    def __init__(self, view: "ScrollableOutputView") -> None:
        self.view = view

    @property
    def cursor_position_row(self) -> int:
        return self.view.scroll_top


class ScrollableTextControl(FormattedTextControl):
    def __init__(self, view: "ScrollableOutputView") -> None:
        self.view = view
        super().__init__(
            view.get_rendered_fragments,
            focusable=True,
            show_cursor=False,
            get_cursor_position=view.get_cursor_position,
        )

    def mouse_handler(self, mouse_event: MouseEvent):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self.view.scroll(-5)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self.view.scroll(5)
            return None
        return super().mouse_handler(mouse_event)


class ScrollableOutputView:
    def __init__(
        self,
        text: str,
        style: str = "",
        height: Dimension | None = None,
        fragment_styler: Callable[[str], list[tuple[str, str]]] = style_output_fragments,
    ) -> None:
        self._text = text
        self._rendered_text = text
        self.fragment_styler = fragment_styler
        self.display_line_starts = build_line_starts(text)
        self.display_width: int | None = None
        self.scroll_top = 0
        self.follow_bottom = True
        self.buffer = ScrollPositionBuffer(self)
        self.document = ScrollPositionDocument(self)
        self.control = ScrollableTextControl(self)
        self.window = Window(
            content=self.control,
            height=height,
            wrap_lines=False,
            always_hide_cursor=True,
            right_margins=[ScrollbarMargin(display_arrows=True)],
            get_vertical_scroll=lambda _: self.scroll_top,
            style=style,
        )

    def __pt_container__(self) -> Window:
        return self.window

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self.set_text(value)

    @property
    def cursor_position(self) -> int:
        if self.is_at_bottom():
            return len(self._text)
        index = max(0, min(self.scroll_top, len(self.display_line_starts) - 1))
        return self.display_line_starts[index]

    @cursor_position.setter
    def cursor_position(self, value: int) -> None:
        self.ensure_render_cache()
        if value <= 0:
            self.scroll_to_start()
            return
        if value >= len(self._text):
            self.scroll_to_end()
            return
        self.scroll_top = line_number_for_position(self.display_line_starts, value)
        self.follow_bottom = False

    def set_text(self, text: str, follow_bottom: bool | None = None) -> None:
        was_at_bottom = self.is_at_bottom()
        self._text = text
        self.display_width = None
        self.ensure_render_cache()
        should_follow = was_at_bottom if follow_bottom is None else follow_bottom
        if should_follow:
            self.scroll_to_end()
        else:
            self.scroll_top = min(self.scroll_top, self.max_scroll_top())
            self.follow_bottom = False

    def get_rendered_text(self) -> str:
        self.ensure_render_cache()
        return self._rendered_text

    def get_rendered_fragments(self) -> list[tuple[str, str]]:
        self.ensure_render_cache()
        return self.fragment_styler(self._rendered_text)

    def get_cursor_position(self) -> Point:
        self.ensure_render_cache()
        line_count = max(1, len(self.display_line_starts))
        return Point(x=0, y=max(0, min(self.scroll_top, line_count - 1)))

    def ensure_render_cache(self) -> None:
        width = self.get_display_width()
        if self.display_width == width:
            return
        self.display_width = width
        self._rendered_text, self.display_line_starts = wrap_text_for_display(self._text, width)
        self.scroll_top = min(self.scroll_top, self.max_scroll_top())

    def get_display_width(self) -> int:
        render_info = self.window.render_info
        if render_info is not None and render_info.window_width > 1:
            return render_info.window_width - 1
        return 80

    def get_visible_height(self) -> int:
        render_info = self.window.render_info
        if render_info is not None and render_info.window_height > 0:
            return render_info.window_height
        return 20

    def max_scroll_top(self) -> int:
        return max(0, len(self.display_line_starts) - self.get_visible_height())

    def is_at_bottom(self) -> bool:
        self.ensure_render_cache()
        return self.follow_bottom or self.scroll_top >= self.max_scroll_top()

    def scroll(self, line_delta: int) -> None:
        self.ensure_render_cache()
        self.scroll_top = max(0, min(self.scroll_top + line_delta, self.max_scroll_top()))
        self.follow_bottom = self.scroll_top >= self.max_scroll_top()

    def scroll_to_start(self) -> None:
        self.ensure_render_cache()
        self.scroll_top = 0
        self.follow_bottom = False

    def scroll_to_end(self) -> None:
        self.ensure_render_cache()
        self.scroll_top = self.max_scroll_top()
        self.follow_bottom = True


class PromptTui:
    def __init__(
        self,
        options: PromptTuiOptions | None = None,
        cwd: Path | None = None,
        initial_session: SessionState | None = None,
    ) -> None:
        resolved_options = options or PromptTuiOptions(cwd=cwd or Path.cwd(), limits=LoopLimits())
        self.options = resolved_options
        memory_manager = (
            MemoryManager(project_memory_path(resolved_options.cwd))
            if initial_session is not None
            else MemoryManager(":memory:")
        )
        self.manager = SessionManager(
            resolved_options.cwd,
            runtime=None,
            default_limits=resolved_options.limits,
            dry_run=resolved_options.dry_run,
            memory_manager=memory_manager,
            initial_session=initial_session,
        )
        self.is_running = False
        self.is_streaming_artifact = False
        self.visible_trace_count = 0
        self.trace_reveal_batch_size = 3
        self.follow_output = True
        self.completed_transcript_blocks: list[str] = self._initial_completed_transcript_blocks(initial_session)
        self.confirmation_in_progress = False
        self.confirmation_scroll_batch_size = 5
        self.confirmation_render_signature: tuple[str, str, str, str, bool] | None = None
        self._agent_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=AGENT_THREAD_NAME_PREFIX)
        self._agent_executor_shutdown = False
        initial_output = (
            self.format_history()
            if self.completed_transcript_blocks
            else self._format_initial_welcome()
        )
        self.output_line_starts = build_line_starts(initial_output)
        self.output_display_width: int | None = None
        self.output_display_line_starts = self.output_line_starts
        self.output = ScrollableOutputView(initial_output, style="class:panel")
        self.output.scroll_to_end()
        self.confirmation_panel_view = ScrollableOutputView(
            "",
            style="class:confirmation",
            height=Dimension(preferred=10, max=14),
            fragment_styler=style_confirmation_fragments,
        )
        self.refresh_confirmation_panel()
        self.messages = self.output
        self.artifact = self.output
        self.input = TextArea(
            height=Dimension(preferred=3, min=2, max=4),
            multiline=True,
            wrap_lines=True,
            prompt="> ",
            style="class:input",
        )
        self.status = Label(format_status(self.manager.current), style="class:status")
        self.app = self._build_app()

    def _format_initial_welcome(self) -> str:
        config = AppConfig.from_env(self.options.cwd / ".env")
        llm_configured = (
            config.llm_provider == "openai-compatible"
            and bool(config.llm_base_url)
            and bool(config.llm_api_key)
        )
        return format_welcome(
            self.manager.runtime.default_limits,
            llm_model=config.llm_model,
            llm_configured=llm_configured,
            llm_config_source=config.llm_config_source,
        )

    def _initial_completed_transcript_blocks(self, initial_session: SessionState | None) -> list[str]:
        if initial_session is None or (not initial_session.messages and initial_session.active_task is None):
            return []
        return [self._format_resume_history(initial_session)]

    def _format_resume_history(self, session: SessionState) -> str:
        active_task_is_terminal = session.active_task is not None and session.active_task.status in {"done", "failed"}
        blocks = []
        if session.active_task is not None:
            if active_task_is_terminal:
                blocks.append(self._format_resume_task_summary(session))
            else:
                blocks.append(format_transcript(session, show_process=True))
        blocks.append(format_section("历史概览", self._format_resume_message_overview(session, omit_last_agent=active_task_is_terminal)))
        return "\n\n".join(block for block in blocks if block)

    def _format_resume_task_summary(self, session: SessionState) -> str:
        task = session.active_task
        if task is None:
            return ""
        lines = [
            f"- Run ID: {task.run_id}",
            f"- 状态：{format_phase_label(task)}",
            f"- 执行步数：{task.step_count}",
            f"- 工具观察：{len(task.observations)} 条",
        ]
        result = (task.result or "").strip()
        if not result and task.errors:
            result = "\n".join(f"- {error.code}: {error.message}" for error in task.errors)
        body = render_markdown_result(_short_resume_text(result, limit=RESUME_RESULT_PREVIEW_CHARS) or "暂无结果正文。")
        return format_section("最近任务", "\n".join([*lines, "", "结果摘要", body]))

    def _format_resume_message_overview(self, session: SessionState, omit_last_agent: bool = False) -> str:
        messages = [
            message
            for message in session.messages
            if message.role == "user" and _short_resume_text(message.content, limit=RESUME_HISTORY_PREVIEW_CHARS)
        ]
        if not messages:
            messages = [
                message
                for message in session.messages
                if message.role == "agent" and _short_resume_text(message.content, limit=RESUME_HISTORY_PREVIEW_CHARS)
            ]
            if omit_last_agent and messages and messages[-1].role == "agent":
                messages = messages[:-1]
        if not messages:
            return "暂无历史消息。"

        hidden_count = max(0, len(messages) - RESUME_HISTORY_MESSAGE_LIMIT)
        visible_messages = messages[-RESUME_HISTORY_MESSAGE_LIMIT:]
        lines = []
        if hidden_count:
            lines.append(f"- 已折叠较早 {hidden_count} 条历史问题。")
        for message in visible_messages:
            label = {"user": "你", "agent": "Agent"}.get(message.role, message.role)
            preview = _short_resume_text(message.content, limit=RESUME_HISTORY_PREVIEW_CHARS)
            lines.append(f"- {label}：{preview or '[空消息]'}")
        return "\n".join(lines)

    def _build_app(self) -> Application:
        install_shift_enter_mapping()
        key_bindings = KeyBindings()
        input_focused = has_focus(self.input)
        confirmation_visible = Condition(self.should_show_confirmation_overlay)
        confirmation_active = Condition(self.should_accept_confirmation_input)

        @key_bindings.add("enter", filter=input_focused & ~confirmation_active)
        def _send_message(_) -> None:
            self.send_current_input()

        @key_bindings.add("enter", filter=confirmation_active)
        def _confirm_confirmation(_) -> None:
            self.submit_confirmation_input(self.input.text)

        @key_bindings.add("c-j", filter=input_focused)
        def _insert_newline(_) -> None:
            self.insert_input_newline()

        @key_bindings.add("escape", filter=confirmation_active)
        def _reject_confirmation(_) -> None:
            self.reject_pending_confirmation()

        @key_bindings.add("up", filter=confirmation_active)
        def _confirmation_up(_) -> None:
            self.scroll_confirmation(-self.confirmation_scroll_batch_size)

        @key_bindings.add("down", filter=confirmation_active)
        def _confirmation_down(_) -> None:
            self.scroll_confirmation(self.confirmation_scroll_batch_size)

        @key_bindings.add("c-c")
        def _exit(event) -> None:
            self.request_exit(event.app)

        @key_bindings.add("tab")
        def _toggle_focus(event) -> None:
            if event.app.layout.current_control is self.output.control:
                event.app.layout.focus(self.input)
            else:
                event.app.layout.focus(self.output)

        @key_bindings.add("pagedown")
        def _output_page_down(event) -> None:
            self.scroll_output(30)
            event.app.layout.focus(self.output)

        @key_bindings.add("pageup")
        def _output_page_up(event) -> None:
            self.scroll_output(-30)
            event.app.layout.focus(self.output)

        @key_bindings.add("home")
        def _output_home(event) -> None:
            self.scroll_output_to_start()
            event.app.layout.focus(self.output)

        @key_bindings.add("end")
        def _output_end(event) -> None:
            self.scroll_output_to_end()
            event.app.layout.focus(self.output)

        @key_bindings.add(Keys.ScrollDown)
        def _output_wheel_down(event) -> None:
            self.scroll_output(5)
            event.app.layout.focus(self.output)

        @key_bindings.add(Keys.ScrollUp)
        def _output_wheel_up(event) -> None:
            self.scroll_output(-5)
            event.app.layout.focus(self.output)

        main_body = HSplit(
            [
                self.output,
                self.status,
                self.input,
            ],
            padding=1,
        )
        body = FloatContainer(
            content=main_body,
            floats=[
                Float(
                    content=ConditionalContainer(
                        Frame(self.confirmation_panel_view, title="文件修改确认"),
                        filter=confirmation_visible,
                    ),
                    bottom=1,
                    left=2,
                    right=2,
                )
            ],
        )
        style = Style.from_dict(
            {
                "frame.border": "#6ea8a1",
                "frame.label": "#f3f0e8",
                "panel": "bg:#172026 #d7dedb",
                "process": "bg:#172026 #74827e",
                "process.reasoning": "bg:#172026 #d7dedb",
                "process.diff": "bg:#172026 #d7dedb",
                "process.diff.header": "bg:#172026 #8fa19c",
                "process.diff.add": "bg:#123625 #9ff2c2",
                "process.diff.remove": "bg:#3a181b #fca5a5",
                "input": "bg:#121a20 #f3f0e8",
                "status": "bg:#0f171b #c7d4cf",
                "confirmation": "bg:#1f2933 #f3f0e8",
                "confirmation.title": "bold #f3f0e8",
                "confirmation.body": "#d7dedb",
                "confirmation.diff": "bg:#1f2933 #d7dedb",
                "confirmation.diff.header": "bg:#1f2933 #8fa19c",
                "confirmation.diff.add": "bg:#123625 #9ff2c2",
                "confirmation.diff.remove": "bg:#3a181b #fca5a5",
                "confirmation.hint": "#8fa19c",
            }
        )
        return Application(
            layout=Layout(body, focused_element=self.input),
            key_bindings=key_bindings,
            full_screen=True,
            mouse_support=True,
            style=style,
        )

    def send_current_input(self) -> None:
        if self.is_running or self.manager.current.pending_confirmation is not None:
            return

        content = self.input.text.strip()
        if not content:
            return

        self.input.text = ""
        if self.is_direct_command(content):
            self.run_direct_command(content)
            return

        previous_task = self.manager.current.active_task
        if previous_task is not None and previous_task.result:
            self.manager.current.messages.append(Message.system("已有产物:\n" f"{previous_task.result}"))
        self.manager.current.messages.append(Message.user(content))
        task = TaskState.create(
            goal=content,
            cwd=self.manager.current.cwd,
            limits=self.manager.runtime.default_limits,
        )
        task.metadata["compression_snapshot_start_index"] = len(self.manager.current.compression_snapshots)
        self.manager._ensure_session_model_context_limit()
        task.model_context_limit = self.manager.current.model_context_limit
        self.manager.current.active_task = task
        self.visible_trace_count = 0
        self.set_output_text(self.format_transcript_with_history(self.manager.current, show_process=True), force_follow=True)
        self.is_running = True
        self.status.text = format_status(self.manager.current)
        self.app.layout.focus(self.input)
        self.app.invalidate()
        self.start_agent_turn(content)

    def is_direct_command(self, content: str) -> bool:
        normalized = content.strip().lower()
        if normalized in HELP_COMMANDS or normalized in SAVE_CONTEXT_COMMANDS or normalized in COMPACT_CONTEXT_COMMANDS:
            return True
        return normalized.startswith("忘记")

    def run_direct_command(self, content: str) -> None:
        self.manager.current = self.manager.handle_user_message(content)
        self.visible_trace_count = 0
        self.set_output_text(self.format_command_output(self.manager.current), force_follow=True)
        self.is_running = False
        self.status.text = format_status(self.manager.current)
        self.app.layout.focus(self.input)
        self.app.invalidate()

    def format_command_output(self, session: SessionState) -> str:
        current = format_section("当前会话", format_messages(session))
        blocks = [*self.completed_transcript_blocks, current]
        return "\n\n".join(block for block in blocks if block)

    def confirm_pending_confirmation(self) -> None:
        if self.manager.current.pending_confirmation is None:
            return
        self.confirmation_in_progress = True
        self.is_running = True
        self.status.text = format_status(self.manager.current, is_running=True)
        self.refresh_confirmation_panel(force=True)
        self.app.layout.focus(self.input)
        self.app.invalidate()
        self.start_agent_turn("确认", confirmation_turn=True)

    def submit_confirmation_input(self, content: str) -> None:
        normalized = content.strip()
        if not normalized:
            self.confirm_pending_confirmation()
            return
        self.input.text = ""
        if normalized in {"确认", "y", "yes", "是"}:
            self.confirm_pending_confirmation()
            return
        self.manager.current = self.manager.handle_user_message(normalized)
        self.refresh_confirmation_panel()
        self.status.text = format_status(self.manager.current)
        self.set_output_text(self.format_transcript_with_history(self.manager.current, show_process=True), force_follow=True)
        self.app.layout.focus(self.input)
        self.app.invalidate()

    def reject_pending_confirmation(self) -> None:
        if self.manager.current.pending_confirmation is None:
            return
        self.manager.current = self.manager.reject_pending_confirmation()
        self.manager._save_current(self.manager.current)
        self.refresh_confirmation_panel()
        self.status.text = format_status(self.manager.current)
        self.set_output_text(self.format_transcript_with_history(self.manager.current, show_process=True), force_follow=True)
        self.app.layout.focus(self.input)
        self.app.invalidate()

    def should_show_confirmation_overlay(self) -> bool:
        if self.manager.current.pending_confirmation is None:
            return False
        return True

    def should_accept_confirmation_input(self) -> bool:
        return self.manager.current.pending_confirmation is not None and not self.confirmation_in_progress

    def scroll_confirmation(self, line_delta: int) -> None:
        if self.manager.current.pending_confirmation is None:
            return
        self.confirmation_panel_view.scroll(line_delta)
        self.app.invalidate()

    def start_agent_turn(self, content: str, confirmation_turn: bool = False) -> None:
        self.app.create_background_task(self.run_agent_turn(content, confirmation_turn=confirmation_turn))
        self.app.create_background_task(self.poll_runtime_progress())

    async def poll_runtime_progress(self) -> None:
        while self.is_running:
            self.refresh_confirmation_panel()
            if not self.is_streaming_artifact:
                self.render_progress()
            await asyncio.sleep(0.2)

    def render_progress(self) -> None:
        self.refresh_confirmation_panel()
        if not self.is_output_at_bottom():
            self.status.text = format_status(self.manager.current)
            self.app.invalidate()
            return

        self.advance_visible_trace_count()
        self.set_output_text(
            self.format_transcript_with_history(
                self.manager.current,
                show_process=True,
                visible_trace_count=self.visible_trace_count,
            )
        )
        self.status.text = format_status(self.manager.current)
        self.app.invalidate()

    def advance_visible_trace_count(self) -> None:
        task = self.manager.current.active_task
        if task is None:
            self.visible_trace_count = 0
            return
        total = len(task.trace_events)
        if self.visible_trace_count >= total:
            return
        self.visible_trace_count = min(total, self.visible_trace_count + self.trace_reveal_batch_size)

    async def stream_session(self, session: SessionState) -> None:
        if session.active_task is None:
            raise RuntimeError("runtime returned no active task")
        self.is_streaming_artifact = True
        self.status.text = format_status(session)
        if session.pending_confirmation is not None:
            self.refresh_confirmation_panel(session.pending_confirmation)
        self.app.layout.focus(self.input)
        result = session.active_task.result
        visible = ""
        try:
            for index in range(0, len(result), 8):
                visible = result[: index + 8]
                self.set_output_text(self.format_transcript_with_history(session, show_process=False, result_override=visible))
                self.app.invalidate()
                await asyncio.sleep(0.03)
            self.append_completed_transcript(session)
            self.set_output_text(self.format_history())
            self.app.invalidate()
        finally:
            self.is_streaming_artifact = False
            self.is_running = False
            self.status.text = format_status(session, is_running=False)
            self.app.invalidate()

    def render_unexpected_error(self, error: Exception) -> None:
        result = f"执行失败：{error}"
        task = self.manager.current.active_task
        if task is not None:
            task.status = "failed"
            task.result = result
            task.errors.append(AgentError(code="UNKNOWN_ERROR", message=str(error), retryable=False))
        self.manager.current.messages.append(Message.system(result))
        save_current = getattr(self.manager, "_save_current", None)
        if callable(save_current):
            save_current(self.manager.current)
        self.is_running = False
        self.is_streaming_artifact = False
        self.set_output_text(f"{self.output.text}\n\n最终产物\n{result}")
        self.status.text = "failed"
        self.app.layout.focus(self.input)
        self.app.invalidate()

    def refresh_confirmation_panel(self, pending: PendingConfirmation | None = None, force: bool = False) -> None:
        if pending is None:
            pending = self.manager.current.pending_confirmation
        if pending is None:
            self.confirmation_render_signature = None
            self.confirmation_panel_view.set_text("", follow_bottom=False)
            return
        signature = self.confirmation_signature(pending)
        if not force and signature == self.confirmation_render_signature:
            return
        self.confirmation_render_signature = signature
        text = self.format_confirmation_text(pending)
        self.confirmation_panel_view.set_text(text, follow_bottom=False)
        self.confirmation_panel_view.scroll_to_start()

    def confirmation_signature(self, pending: PendingConfirmation) -> tuple[str, str, str, str, bool]:
        return (
            pending.tool_name,
            pending.tool_call_id,
            pending.prompt or pending.summary,
            pending.diff_preview or "",
            self.confirmation_in_progress,
        )

    def format_confirmation_text(self, pending: PendingConfirmation) -> str:
        title = pending.prompt or pending.summary or "即将修改文件"
        if self.confirmation_in_progress:
            return "\n".join(
                [
                    "确认写入",
                    "",
                    title,
                    "",
                    "已确认，正在执行写入和后续流程...",
                ]
            )
        diff_preview = (pending.diff_preview or "").strip()
        lines = [
            "确认写入",
            "",
            title,
        ]
        if pending.tool_name:
            lines.append(f"工具：{pending.tool_name}")
        if diff_preview:
            lines.extend(["", "变更预览", "", diff_preview])
        lines.extend(["", "↑/↓ 滚动预览，Enter 确认，Esc 拒绝"])
        return "\n".join(lines).strip()

    @property
    def confirmation_panel(self) -> Window:
        return self.confirmation_panel_view.window

    async def run_agent_turn(self, content: str, confirmation_turn: bool = False) -> None:
        try:
            session = await self._run_manager_message(content)
            await self.stream_session(session)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.handle_interrupted_execution()
            return
        except Exception as error:
            self.render_unexpected_error(error)
            return
        finally:
            if confirmation_turn:
                self.confirmation_in_progress = False
                self.refresh_confirmation_panel()
                self.status.text = format_status(self.manager.current, is_running=self.is_running)
                self.app.invalidate()

    async def _run_manager_message(self, content: str) -> SessionState:
        loop = asyncio.get_running_loop()
        if self._agent_executor_shutdown:
            self._agent_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=AGENT_THREAD_NAME_PREFIX)
            self._agent_executor_shutdown = False
        return await loop.run_in_executor(self._agent_executor, self.manager.handle_user_message, content, False)

    def insert_input_newline(self) -> None:
        text, cursor = insert_newline(self.input.text, self.input.buffer.cursor_position)
        self.input.text = text
        self.input.buffer.cursor_position = cursor
        self.app.invalidate()

    def set_output_text(self, text: str, force_follow: bool = False) -> None:
        should_follow_bottom = force_follow or self.is_output_at_bottom()
        self.output_line_starts = build_line_starts(text)
        self.output_display_width = None
        self.output.set_text(text, follow_bottom=should_follow_bottom)
        self.output_display_line_starts = self.output.display_line_starts
        if should_follow_bottom:
            self.scroll_output_to_end()
        else:
            self.follow_output = False

    def append_completed_transcript(self, session: SessionState) -> None:
        if session.pending_confirmation is not None:
            return
        if session.active_task is None or session.active_task.status not in {"done", "failed"}:
            return
        block = format_transcript(session, show_process=False)
        if not block:
            return
        if not self.completed_transcript_blocks or self.completed_transcript_blocks[-1] != block:
            self.completed_transcript_blocks.append(block)

    def format_transcript_with_history(
        self,
        session: SessionState,
        *,
        show_process: bool,
        result_override: str | None = None,
        visible_trace_count: int | None = None,
    ) -> str:
        current = format_transcript(
            session,
            show_process=show_process,
            result_override=result_override,
            visible_trace_count=visible_trace_count,
        )
        blocks = [*self.completed_transcript_blocks, current]
        return "\n\n".join(block for block in blocks if block)

    def format_history(self) -> str:
        return "\n\n".join(self.completed_transcript_blocks)

    def is_output_at_bottom(self) -> bool:
        return self.output.is_at_bottom()

    def scroll_output(self, line_delta: int) -> None:
        self.output.scroll(line_delta)
        self.output_display_line_starts = self.output.display_line_starts
        self.follow_output = self.is_output_at_bottom()
        self.app.invalidate()

    def get_output_scroll_line_starts(self) -> list[int]:
        self.output.ensure_render_cache()
        self.output_display_width = self.output.display_width
        self.output_display_line_starts = self.output.display_line_starts
        return self.output_display_line_starts

    def get_output_display_width(self) -> int:
        return self.output.get_display_width()

    def scroll_output_to_start(self) -> None:
        self.output.scroll_to_start()
        self.follow_output = False
        self.app.invalidate()

    def scroll_output_to_end(self) -> None:
        self.output.scroll_to_end()
        self.follow_output = True
        self.app.invalidate()

    def run(self) -> None:
        try:
            self.app.run()
        except KeyboardInterrupt:
            self.handle_interrupted_execution()
        finally:
            self.shutdown_background_execution(detach=True)
            self.manager.runtime.react_loop.executor.shutdown(detach=True)

    def request_exit(self, app: Application | None = None) -> None:
        if self.is_running or self.manager.current.active_task is not None:
            self.handle_interrupted_execution()
        else:
            self.shutdown_background_execution(detach=True)
            self.manager.runtime.react_loop.executor.shutdown(detach=True)
        target_app = app or self.app
        target_app.exit()

    def shutdown_background_execution(self, detach: bool = False) -> None:
        if self._agent_executor_shutdown:
            return
        threads = list(getattr(self._agent_executor, "_threads", ()))
        self._agent_executor.shutdown(wait=False, cancel_futures=True)
        if detach:
            _detach_threads_from_python_shutdown(threads)
        self._agent_executor_shutdown = True

    def handle_interrupted_execution(self) -> None:
        self.manager._mark_current_interrupted()
        self.manager._save_current(self.manager.current)
        self.is_running = False
        self.is_streaming_artifact = False
        self.status.text = format_status(self.manager.current, is_running=False)
        self.shutdown_background_execution(detach=True)
        self.manager.runtime.react_loop.executor.shutdown(detach=True)
        self.app.invalidate()
