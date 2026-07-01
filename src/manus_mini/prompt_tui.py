from __future__ import annotations

import asyncio
import json
from bisect import bisect_right
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.filters import has_focus
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, Label, TextArea

from manus_mini.models import LoopLimits, Message, Observation, SessionState, TaskState, TraceEvent
from manus_mini.redaction import redact_sensitive_text
from manus_mini.session import SessionManager

SHIFT_ENTER_SEQUENCES = (
    "\x1b[27;2;13~",
    "\x1b[13;2u",
)


def install_shift_enter_mapping() -> None:
    from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
    from prompt_toolkit.keys import Keys

    for sequence in SHIFT_ENTER_SEQUENCES:
        ANSI_SEQUENCES[sequence] = Keys.ControlJ


def format_messages(
    session: SessionState,
    omit_last_agent: bool = False,
) -> str:
    messages = list(session.messages)
    if omit_last_agent and messages and messages[-1].role == "agent":
        messages = messages[:-1]
    if not messages:
        return "等待输入..."
    lines: list[str] = []
    for message in messages:
        lines.append(format_message_block(message))
    return "\n\n".join(lines)


SECTION_SEPARATOR = "────────────────────────────────────────"
USER_MESSAGE_BORDER = "────────────────────────────────────────"


def format_message_block(message: Message) -> str:
    speaker = "你" if message.role == "user" else "Agent"
    if message.role != "user":
        return f"{speaker}: {message.content}"

    lines = [f"┌─ {speaker} ───────────────────────────────────"]
    for line in message.content.splitlines() or [""]:
        lines.append(f"│  {line}")
    lines.append(f"└{USER_MESSAGE_BORDER}")
    return "\n".join(lines)


def format_section(title: str, body: str) -> str:
    return f"{SECTION_SEPARATOR}\n{title}\n{body}"


def format_user_question(session: SessionState) -> str:
    for message in reversed(session.messages):
        if message.role == "user":
            return message.content
    if session.active_task is not None:
        return session.active_task.goal
    return "等待输入..."


def format_process(
    session: SessionState,
    max_events: int = 8,
    visible_trace_count: int | None = None,
    full_history: bool = False,
) -> str:
    task = session.active_task
    if task is None:
        return "当前步骤\n等待用户输入。"

    trace_events = task.trace_events
    if visible_trace_count is not None:
        trace_events = trace_events[: max(0, visible_trace_count)]

    sections = [
        format_task_overview(task),
        format_tool_activity(task, visible_events=trace_events, limit=None if full_history else 5),
        format_recent_events(
            trace_events,
            max_events=len(trace_events) if full_history else max_events,
            total_events=len(task.trace_events),
        ),
    ]
    return "\n\n".join(section for section in sections if section)


def format_task_overview(task: TaskState) -> str:
    current = f"第 {max(task.step_count, 0)} 步" if task.step_count else "准备中"
    return "\n".join(
        [
            "当前步骤",
            f"- 任务：{task.goal}",
            f"- 阶段：{format_phase_label(task)}",
            f"- 当前动作：{format_current_action(task)}",
            f"- 进度：{current} / 最多 {task.limits.max_engineering_steps} 步",
            f"- 状态：{task.status}",
        ]
    )


def format_tool_activity(task: TaskState, visible_events: list[TraceEvent] | None = None, limit: int | None = 5) -> str:
    events = visible_events if visible_events is not None else task.trace_events
    tool_call_lines = []
    for event in events:
        for call in event.data.get("tool_calls", []) or []:
            if not isinstance(call, dict):
                continue
            tool_call_id = call.get("id", "unknown")
            name = call.get("name", "unknown")
            args = format_inline_args(call.get("args", {}))
            tool_call_lines.append(f"- {name}({tool_call_id}) {args}".rstrip())

    tool_return_lines = []
    for event in events:
        if event.phase != "tool" or "tool_name" not in event.data:
            continue
        tool_name = event.data.get("tool_name", "unknown")
        tool_call_id = event.data.get("tool_call_id", "unknown")
        status = format_tool_return_status(event.data)
        summary = event.data.get("summary") or event.message
        preview = event.data.get("content_preview") or ""
        line = f"- {tool_name}({tool_call_id}) {status}: {redact_sensitive_text(str(summary))}"
        if preview:
            line += f"\n  返回预览：{format_display_value(str(preview))}"
        tool_return_lines.append(line)

    if not tool_return_lines:
        tool_return_lines.extend(format_observation_return_lines(task.observations))

    if not tool_call_lines and not tool_return_lines:
        return "工具活动\n- 暂无工具调用。"

    sections = ["工具活动"]
    if tool_call_lines:
        sections.append("工具调用")
        sections.extend(tool_call_lines if limit is None else tool_call_lines[-limit:])
    if tool_return_lines:
        sections.append("工具返回")
        sections.extend(tool_return_lines if limit is None else tool_return_lines[-limit:])
    return "\n".join(sections)


def format_observation_return_lines(observations: list[Observation]) -> list[str]:
    lines = []
    for observation in observations[-5:]:
        status = "成功" if observation.ok else "失败"
        summary = redact_sensitive_text(observation.summary)
        tool_call_id = observation.tool_call_id or "unknown"
        line = f"- {tool_call_id} {status}: {summary}"
        if observation.content:
            line += f"\n  返回预览：{redact_sensitive_text(_short_text(observation.content))}"
        lines.append(line)
    return lines


def format_tool_return_status(data: dict) -> str:
    if "ok" not in data:
        return "已返回"
    return "成功" if data.get("ok") else "失败"


def _short_text(content: str, limit: int = 160) -> str:
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def format_recent_events(
    events: list[TraceEvent],
    max_events: int = 8,
    total_events: int | None = None,
) -> str:
    if not events:
        return "最近过程\n- 等待执行..."

    visible_events = events[-max_events:]
    lines = ["最近过程"]
    total = total_events if total_events is not None else len(events)
    if len(events) > max_events:
        lines.append(f"- 仅展示最近 {max_events} 条，已隐藏 {len(events) - max_events} 条较早过程。")
    if total > len(events):
        lines.append(f"- 正在逐步展示过程：已显示 {len(events)}/{total} 条。")
    for index, event in enumerate(visible_events, start=max(1, len(events) - len(visible_events) + 1)):
        lines.append(f"- {index}. {format_event_summary(event)}")
    return "\n".join(lines)


def format_event_details(data: dict, ignored_keys: set[str] | None = None) -> str:
    ignored = ignored_keys or set()
    parts = []
    for key, value in data.items():
        if key in ignored:
            continue
        if isinstance(value, list | dict):
            continue
        parts.append(f"{key}: {format_display_value(value, limit=80)}")
    return " | ".join(parts)


def format_event_summary(event: TraceEvent) -> str:
    if event.phase == "llm":
        tool_calls = event.data.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            calls = []
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                name = call.get("name", "unknown")
                tool_call_id = call.get("id", "unknown")
                calls.append(f"{name}({tool_call_id})")
            if calls:
                return f"LLM：准备调用：{', '.join(calls)}"
        preview = event.data.get("content_preview")
        if preview:
            return f"LLM：返回内容 {_short_text(str(preview), limit=80)}"
        details = format_event_details(event.data)
        suffix = f"（{details}）" if details else ""
        return f"LLM：{event.message}{suffix}"

    if event.phase == "tool":
        tool_name = event.data.get("tool_name")
        tool_call_id = event.data.get("tool_call_id")
        if tool_name or tool_call_id:
            status = format_tool_return_status(event.data)
            summary = redact_sensitive_text(str(event.data.get("summary") or event.message))
            error_code = event.data.get("error_code")
            suffix = f"，错误码 {error_code}" if error_code else ""
            return f"工具返回：{tool_name or 'unknown'}({tool_call_id or 'unknown'}) {status}，{summary}{suffix}"
        batches = event.data.get("batches")
        if batches:
            return f"工具调度：{len(batches)} 个批次"
        return f"工具：{event.message}"

    if event.phase == "react":
        iteration = event.data.get("iteration")
        if iteration:
            return f"ReAct：第 {iteration} 轮开始"
        return f"ReAct：{event.message}"

    if event.phase == "reflection":
        return f"反思：{event.message}"

    if event.phase == "runtime":
        code = event.data.get("code")
        if code:
            return f"运行时：{event.message}（{code}）"
        return f"运行时：{event.message}"

    return event.message


def format_artifact(session: SessionState, result_override: str | None = None) -> str:
    if session.active_task is None:
        return "当前产物会显示在这里"
    task = session.active_task
    result = result_override if result_override is not None else task.result
    result = result or "生成中..."
    return "\n".join(
        [
            "完成摘要",
            f"- 状态：{task.status}",
            f"- 执行步数：{task.step_count}",
            f"- 工具观察：{len(task.observations)} 条",
            "",
            "结果正文",
            result,
        ]
    )


def format_transcript(
    session: SessionState,
    *,
    show_process: bool,
    result_override: str | None = None,
    visible_trace_count: int | None = None,
) -> str:
    sections = [
        format_section("用户问题", format_user_question(session)),
        format_section("对话记录", format_messages(session, omit_last_agent=not show_process)),
    ]
    if show_process or session.active_task is not None:
        sections.append(
            format_section(
                "执行过程",
                format_process(
                    session,
                    visible_trace_count=visible_trace_count,
                    full_history=not show_process,
                ),
            )
        )
    if not show_process:
        sections.append(format_section("最终产物", format_artifact(session, result_override=result_override)))
    return "\n\n".join(sections)


def format_welcome(limits: LoopLimits) -> str:
    return "\n".join(
        [
            "欢迎使用 Manus Mini",
            "",
            "你可以在这里连续对话，让 Agent 读取项目、调用工具、生成报告或写入文件。",
            "",
            "运行设置",
            f"- 外层工程循环上限：{limits.max_engineering_steps} 轮",
            f"- ReAct 上限：{limits.max_react_iterations} 轮",
            f"- Reflection 上限：{limits.max_reflection_rounds} 轮",
            f"- 单工具重试上限：{limits.max_tool_retries} 次",
            f"- 单轮运行超时：{limits.max_runtime_seconds} 秒",
            "",
            "操作",
            "- Enter 发送",
            "- Shift+Enter 换行",
            "- Tab 切换输入区和输出区",
            "- Ctrl-C 退出",
        ]
    )


def format_trace_data(data: dict) -> str:
    if not data:
        return "-"
    parts: list[str] = []
    for key, value in data.items():
        parts.append(f"{key}: {format_display_value(value)}")
    return " | ".join(parts)


def format_inline_args(args: object) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    return " | ".join(f"{key}: {format_display_value(value, limit=80)}" for key, value in args.items())


def format_display_value(value: object, limit: int = 160) -> str:
    if isinstance(value, str):
        return redact_sensitive_text(_short_text(value, limit=limit))
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int | float):
        return str(value)

    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        rendered = str(value)
    return redact_sensitive_text(_short_text(rendered, limit=limit))


def format_current_action(task: TaskState) -> str:
    for event in reversed(task.trace_events):
        if event.phase == "tool" and "tool_name" in event.data:
            name = event.data.get("tool_name", "unknown")
            tool_call_id = event.data.get("tool_call_id", "unknown")
            status = format_tool_return_status(event.data)
            if status == "已返回":
                return f"工具 {name}({tool_call_id}) 已返回"
            return f"工具 {name}({tool_call_id}) 已{status}返回"

        tool_calls = event.data.get("tool_calls", [])
        if isinstance(tool_calls, list) and tool_calls:
            latest_call = tool_calls[-1]
            if isinstance(latest_call, dict):
                name = latest_call.get("name", "unknown")
                tool_call_id = latest_call.get("id", "unknown")
                return f"准备调用工具 {name}({tool_call_id})"

        if event.message:
            return event.message
    return "等待执行"


def format_phase_label(task: TaskState) -> str:
    labels = {
        "planning": "规划任务",
        "acting": "调用工具",
        "observing": "读取结果",
        "reflecting": "反思校验",
        "reporting": "整理产物",
        "done": "已完成",
        "failed": "执行失败",
    }
    return labels.get(task.status, task.status)


def format_status(session: SessionState, is_running: bool | None = None) -> str:
    task = session.active_task
    if task is None:
        return "idle | Enter 发送 | Shift+Enter 换行 | Ctrl-C 退出"
    state_label = format_status_label(task, is_running=is_running)
    return " | ".join(
        [
            state_label,
            f"阶段 {format_phase_label(task)}",
            f"当前 {format_current_action(task)}",
            "Enter 发送",
            "Shift+Enter 换行",
        ]
    )


def format_status_label(task: TaskState, is_running: bool | None = None) -> str:
    if task.status == "done":
        return "已完成"
    if task.status == "failed":
        return "执行失败"
    if is_running is False:
        return "已结束"
    return "正在执行"


def insert_newline(text: str, cursor_position: int) -> tuple[str, int]:
    cursor = max(0, min(cursor_position, len(text)))
    return f"{text[:cursor]}\n{text[cursor:]}", cursor + 1


def build_line_starts(text: str) -> list[int]:
    starts = [0]
    starts.extend(index + 1 for index, character in enumerate(text) if character == "\n" and index + 1 < len(text))
    return starts


def line_number_for_position(line_starts: list[int], position: int) -> int:
    if not line_starts:
        return 0
    line_index = bisect_right(line_starts, max(0, position)) - 1
    return max(0, min(line_index, len(line_starts) - 1))


class PromptTui:
    def __init__(self, cwd: Path | None = None) -> None:
        self.manager = SessionManager(cwd or Path.cwd())
        self.is_running = False
        self.is_streaming_artifact = False
        self.visible_trace_count = 0
        self.trace_reveal_batch_size = 3
        self.follow_output = True
        initial_output = format_welcome(self.manager.runtime.default_limits)
        self.output_line_starts = build_line_starts(initial_output)
        self.output = TextArea(
            text=initial_output,
            read_only=True,
            scrollbar=True,
            focusable=True,
            wrap_lines=True,
            style="class:panel",
        )
        self.output.buffer.cursor_position = len(self.output.text)
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

        @key_bindings.add("enter", filter=input_focused)
        def _send_message(_) -> None:
            self.send_current_input()

        @key_bindings.add("c-j", filter=input_focused)
        def _insert_newline(_) -> None:
            self.insert_input_newline()

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

        body = HSplit(
            [
                Frame(self.output, title="对话 / 过程 / 产物"),
                self.status,
                Frame(self.input, title="输入区"),
            ],
            padding=1,
        )
        style = Style.from_dict(
            {
                "frame.border": "#6ea8a1",
                "frame.label": "#f3f0e8",
                "panel": "bg:#172026 #d7dedb",
                "input": "bg:#121a20 #f3f0e8",
                "status": "bg:#0f171b #c7d4cf",
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
        if self.is_running:
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

    def start_agent_turn(self, content: str) -> None:
        self.app.create_background_task(self.run_agent_turn(content))
        self.app.create_background_task(self.poll_runtime_progress())

    async def poll_runtime_progress(self) -> None:
        while self.is_running:
            if not self.is_streaming_artifact:
                self.render_progress()
            await asyncio.sleep(0.2)

    def render_progress(self) -> None:
        if not self.is_output_at_bottom():
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

    async def run_agent_turn(self, content: str) -> None:
        try:
            session = await asyncio.to_thread(self.manager.handle_user_message, content, False)
            await self.stream_session(session)
        except Exception as error:
            self.render_unexpected_error(error)
            return

    def insert_input_newline(self) -> None:
        text, cursor = insert_newline(self.input.text, self.input.buffer.cursor_position)
        self.input.text = text
        self.input.buffer.cursor_position = cursor
        self.app.invalidate()

    def set_output_text(self, text: str, force_follow: bool = False) -> None:
        should_follow_bottom = force_follow or self.is_output_at_bottom()
        self.output.text = text
        self.output_line_starts = build_line_starts(text)
        if should_follow_bottom:
            self.scroll_output_to_end()
        else:
            self.follow_output = False

    def is_output_at_bottom(self) -> bool:
        return self.output.buffer.cursor_position >= len(self.output.text)

    def scroll_output(self, line_delta: int) -> None:
        if not self.output_line_starts:
            return

        current_line = line_number_for_position(self.output_line_starts, self.output.buffer.cursor_position)
        target_line = max(0, min(current_line + line_delta, len(self.output_line_starts) - 1))
        self.output.buffer.cursor_position = self.output_line_starts[target_line]
        self.follow_output = self.is_output_at_bottom()
        self.app.invalidate()

    def scroll_output_to_start(self) -> None:
        self.output.buffer.cursor_position = 0
        self.follow_output = False
        self.app.invalidate()

    def scroll_output_to_end(self) -> None:
        self.output.buffer.cursor_position = len(self.output.text)
        self.follow_output = True
        self.app.invalidate()

    def run(self) -> None:
        self.app.run()


def main() -> None:
    PromptTui().run()
