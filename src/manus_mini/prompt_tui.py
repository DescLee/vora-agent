from __future__ import annotations

import asyncio
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

from manus_mini.logging import project_memory_path
from manus_mini.models import LoopLimits, Message, PendingConfirmation, SessionState, TaskState
from manus_mini.memory import MemoryManager
from manus_mini.prompt_tui_formatting import (  # noqa: F401
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
    format_message_block,
    format_messages,
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
from manus_mini.session import SessionManager


@dataclass(slots=True)
class PromptTuiOptions:
    cwd: Path
    limits: LoopLimits
    dry_run: bool = False

SHIFT_ENTER_SEQUENCES = (
    "\x1b[27;2;13~",
    "\x1b[13;2u",
)


def install_shift_enter_mapping() -> None:
    from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
    from prompt_toolkit.keys import Keys

    for sequence in SHIFT_ENTER_SEQUENCES:
        ANSI_SEQUENCES[sequence] = Keys.ControlJ


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
        self.confirmation_in_progress = False
        self.confirmation_scroll_batch_size = 5
        self.confirmation_render_signature: tuple[str, str, str, str, bool] | None = None
        initial_output = (
            format_transcript(self.manager.current, show_process=False)
            if initial_session is not None and initial_session.messages
            else format_welcome(self.manager.runtime.default_limits)
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
            self.confirm_pending_confirmation()

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
            event.app.exit()

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
                Frame(self.output, title="对话 / 过程 / 产物"),
                self.status,
                Frame(self.input, title="输入区"),
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
                "process": "bg:#172026 #8fa19c",
                "input": "bg:#121a20 #f3f0e8",
                "status": "bg:#0f171b #c7d4cf",
                "confirmation": "bg:#1f2933 #f3f0e8",
                "confirmation.title": "bold #f3f0e8",
                "confirmation.body": "#d7dedb",
                "confirmation.diff": "#d7dedb",
                "confirmation.diff.header": "#8fa19c",
                "confirmation.diff.add": "#6ee7a8",
                "confirmation.diff.remove": "#f87171",
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
        self.manager.current.messages.append(Message.user(content))
        self.manager.current.active_task = TaskState.create(goal=content, cwd=self.manager.current.cwd)
        self.visible_trace_count = 0
        self.set_output_text(format_transcript(self.manager.current, show_process=True), force_follow=True)
        self.is_running = True
        self.status.text = format_status(self.manager.current)
        self.app.layout.focus(self.input)
        self.app.invalidate()
        self.start_agent_turn(content)

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

    def reject_pending_confirmation(self) -> None:
        if self.manager.current.pending_confirmation is None:
            return
        self.manager.current = self.manager.reject_pending_confirmation()
        self.manager._save_current(self.manager.current)
        self.refresh_confirmation_panel()
        self.status.text = format_status(self.manager.current)
        self.set_output_text(format_transcript(self.manager.current, show_process=True), force_follow=True)
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
            format_transcript(
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
                self.set_output_text(format_transcript(session, show_process=False, result_override=visible))
                self.app.invalidate()
                await asyncio.sleep(0.03)
            self.set_output_text(format_transcript(session, show_process=False))
            self.app.invalidate()
        finally:
            self.is_streaming_artifact = False
            self.is_running = False
            self.status.text = format_status(session, is_running=False)
            self.app.invalidate()

    def render_unexpected_error(self, error: Exception) -> None:
        self.is_running = False
        self.is_streaming_artifact = False
        self.set_output_text(f"{self.output.text}\n\n最终产物\n执行失败：{error}")
        self.status.text = "failed | Enter 发送 | Shift+Enter 换行"
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
            session = await asyncio.to_thread(self.manager.handle_user_message, content, False)
            await self.stream_session(session)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.manager._save_current(self.manager.current)
            self.is_running = False
            self.is_streaming_artifact = False
            self.status.text = format_status(self.manager.current, is_running=False)
            self.app.invalidate()
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
            self.manager._save_current(self.manager.current)


def main() -> None:
    from manus_mini.cli import main as cli_main

    cli_main()
