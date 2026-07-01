from __future__ import annotations

import asyncio
import json
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.filters import has_focus
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.screen import Point
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import Frame, Label, TextArea
from rich.console import Console
from rich.markdown import Markdown

from manus_mini.context import estimate_context_usage
from manus_mini.models import LoopLimits, Message, Observation, SessionState, TaskState, TraceEvent
from manus_mini.memory import MemoryManager
from manus_mini.redaction import redact_sensitive_text
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
        format_plan(task),
        format_llm_tool_rounds(task, trace_events, limit=None if full_history else 5),
        format_recent_process(task.trace_events),
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
            f"- 进度：{current}",
            f"- 状态：{task.status}",
        ]
    )


def format_plan(task: TaskState) -> str:
    if not task.plan:
        return "执行计划\n- 暂无计划。"
    status_labels = {
        "pending": "待执行",
        "running": "进行中",
        "done": "已完成",
        "skipped": "已跳过",
        "failed": "失败",
    }
    lines = ["执行计划"]
    running_seen = False
    for index, step in enumerate(task.plan, start=1):
        status = step.status
        if status == "pending" and not running_seen and task.status not in {"done", "failed"}:
            current_index = min(max(task.current_step_index + 1, 1), len(task.plan))
            if index == current_index:
                status = "running"
                running_seen = True
        label = status_labels.get(status, status)
        lines.append(f"- [{label}] {step.description}")
    return "\n".join(lines)


def format_llm_activity(events: list[TraceEvent]) -> str:
    lines = []
    for event in events:
        if event.phase != "llm":
            continue
        preview = event.data.get("content_preview")
        if not preview:
            continue
        lines.append(f"- {format_display_value(str(preview), limit=220)}")
    if not lines:
        return "LLM 返回\n- 暂无模型文本。"
    return "\n".join(["LLM 返回", *lines[-5:]])


def format_llm_tool_rounds(task: TaskState, events: list[TraceEvent], limit: int | None = 5) -> str:
    rounds = []
    for index, event in enumerate(events):
        if event.phase != "llm":
            continue
        iteration = event.data.get("iteration")
        next_llm_index = next(
            (
                later_index
                for later_index in range(index + 1, len(events))
                if events[later_index].phase == "llm"
            ),
            len(events),
        )
        round_events = events[index + 1:next_llm_index]
        rounds.append(format_llm_tool_round(event, round_events, task, iteration))
    if not rounds:
        return format_tool_activity(task, visible_events=events, limit=limit)
    visible_rounds = rounds if limit is None else rounds[-limit:]
    return "\n\n".join(visible_rounds)


def format_llm_tool_round(llm_event: TraceEvent, following_events: list[TraceEvent], task: TaskState, iteration) -> str:
    title = f"LLM 回合 {iteration}" if iteration else "LLM 回合"
    lines = [title, "LLM 返回"]
    preview = llm_event.data.get("content_preview")
    lines.append(f"- {format_display_value(str(preview), limit=220)}" if preview else "- 暂无模型文本。")

    tool_calls = [call for call in llm_event.data.get("tool_calls", []) or [] if isinstance(call, dict)]
    if tool_calls:
        batch_groups = group_tool_calls_by_batch(tool_calls, following_events)
        lines.extend(format_tool_batch_sections(batch_groups, following_events, task, iteration))
    return "\n".join(lines)


def group_tool_calls_by_batch(tool_calls: list[dict], events: list[TraceEvent]) -> list[tuple[int, list[dict]]]:
    call_id_to_call = {str(call.get("id", f"call-{index}")): call for index, call in enumerate(tool_calls, start=1)}
    planned_batches = _find_planned_batches(events)
    if planned_batches:
        grouped: list[tuple[int, list[dict]]] = []
        assigned: set[str] = set()
        for batch_index, batch_ids in enumerate(planned_batches, start=1):
            batch_calls = [
                call_id_to_call[call_id]
                for call_id in batch_ids
                if call_id in call_id_to_call
            ]
            if batch_calls:
                grouped.append((batch_index, batch_calls))
                assigned.update(str(call.get("id", "")) for call in batch_calls)
        leftovers = [call for call in tool_calls if str(call.get("id", "")) not in assigned]
        if leftovers:
            grouped.append((len(grouped) + 1, leftovers))
        return grouped
    return [(1, list(tool_calls))]


def _find_planned_batches(events: list[TraceEvent]) -> list[list[str]]:
    for event in events:
        if event.phase != "tool":
            continue
        batches = event.data.get("batches")
        if isinstance(batches, list) and batches:
            planned: list[list[str]] = []
            for batch in batches:
                if not isinstance(batch, list):
                    continue
                batch_ids = [str(item) for item in batch if str(item)]
                if batch_ids:
                    planned.append(batch_ids)
            if planned:
                return planned
    return []


def format_tool_batch_sections(
    batch_groups: list[tuple[int, list[dict]]],
    events: list[TraceEvent],
    task: TaskState,
    iteration,
) -> list[str]:
    if not batch_groups:
        return ["工具调度", "- 暂无工具调用。"]

    total_batches = len(batch_groups)
    lines = ["工具调度", f"- 共 {total_batches} 个批次"]
    for batch_index, batch_calls in batch_groups:
        lines.append(f"- 第 {batch_index} 批（{len(batch_calls)} 个工具）")
        batch_lines = format_tool_batch_lines(batch_index, iteration, batch_calls, events, task)
        lines.extend([f"  {line}" for line in batch_lines] or ["  - 等待工具返回。"])
    return lines


def format_tool_batch_lines(batch_index: int, iteration, tool_calls: list[dict], events: list[TraceEvent], task: TaskState) -> list[str]:
    call_ids = {str(call.get("id", "unknown")) for call in tool_calls}
    prefix = f"{iteration}.{batch_index}" if iteration else str(batch_index)
    lines = []
    event_by_call_id = {
        str(event.data.get("tool_call_id") or ""): event
        for event in events
        if event.phase == "tool" and str(event.data.get("tool_call_id") or "") in call_ids
    }
    observation_by_call = {
        observation.tool_call_id: observation
        for observation in task.observations
        if observation.tool_call_id in call_ids
    }
    for event in events:
        if event.phase == "tool" and str(event.data.get("tool_call_id") or "") in call_ids:
            tool_call_id = str(event.data.get("tool_call_id") or "")
            tool_name = event.data.get("tool_name", "unknown")
            status = format_tool_return_status(event.data)
            summary = event.data.get("summary") or event.message
            preview = event.data.get("content_preview") or ""
            line = f"- 结果 {prefix} {tool_name}({tool_call_id}) {status}: {redact_sensitive_text(str(summary))}"
            if preview:
                line += f"\n  返回预览：{format_display_value(str(preview))}"
            lines.append(line)
    if not lines:
        lines.append(f"- 结果 {prefix} 等待工具返回。")
    call_lines = []
    for call in tool_calls:
        tool_call_id = str(call.get("id", "unknown"))
        name = str(call.get("name", "unknown"))
        args = format_inline_args(call.get("args", {}))
        call_line = f"- 调用 {prefix} {name}({tool_call_id}) {args}".rstrip()
        call_lines.append(call_line)
        observation = observation_by_call.get(tool_call_id)
        if observation is not None and tool_call_id not in event_by_call_id:
            status = "成功" if observation.ok else "失败"
            result_line = f"- 结果 {prefix} {tool_call_id} {status}: {redact_sensitive_text(observation.summary)}"
            if observation.content:
                result_line += f"\n  返回预览：{redact_sensitive_text(_short_text(observation.content))}"
            lines.append(result_line)
    return [*call_lines, *lines]


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

    tool_calls = [
        call
        for event in events
        if event.phase == "llm"
        for call in event.data.get("tool_calls", []) or []
        if isinstance(call, dict)
    ]
    if tool_calls:
        batch_groups = group_tool_calls_by_batch(tool_calls, events)
        sections = ["工具活动", *format_tool_batch_sections(batch_groups, events, task, iteration=None)]
        return "\n".join(sections)

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


def format_recent_process(events: list[TraceEvent]) -> str:
    if not events:
        return "最近过程（折叠）\n- 暂无过程记录。"
    latest = format_event_summary(events[-1])
    total = len(events)
    hidden = max(0, total - 1)
    lines = ["最近过程（折叠）", f"- 共 {total} 条过程记录"]
    if hidden:
        lines.append(f"- 已折叠 {hidden} 条较早过程")
    lines.append(f"- 最新：{latest}")
    return "\n".join(lines)


def _short_text(content: str, limit: int = 160) -> str:
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


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
        if event.data.get("batch_id"):
            batch_id = event.data.get("batch_id")
            parallel = "并行" if event.data.get("parallel") else "串行"
            duration_ms = event.data.get("duration_ms")
            return f"工具批次：{batch_id}（{parallel}，{duration_ms}ms）"
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
    result = render_markdown_result(result or "生成中...")
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


def render_markdown_result(markdown_text: str) -> str:
    try:
        console = Console(width=88, color_system=None, force_terminal=False, soft_wrap=False)
        with console.capture() as capture:
            console.print(Markdown(markdown_text))
        rendered = capture.get()
    except Exception:
        return markdown_text

    lines = [line.rstrip() for line in rendered.splitlines()]
    return "\n".join(lines).strip() or markdown_text


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
            f"- 单工具超时：{limits.max_tool_timeout_seconds} 秒",
            f"- 单轮运行超时：{limits.max_runtime_seconds} 秒",
            "",
            "操作",
            "- Enter 发送",
            "- Shift+Enter 换行",
            "- 输入 `压缩上下文` 或 `/compact` 可手动压缩上下文",
            "- 输入 `/save-context` 可在项目根目录保存当前上下文快照",
            "- 输入 `/help` 可查看全部指令",
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
        "waiting_confirmation": "等待确认",
        "done": "已完成",
        "failed": "执行失败",
    }
    return labels.get(task.status, task.status)


def format_status(session: SessionState, is_running: bool | None = None) -> str:
    task = session.active_task
    if task is None:
        return f"idle | {format_context_usage(session)} | Enter 发送 | Shift+Enter 换行 | Ctrl-C 退出"
    state_label = format_status_label(task, is_running=is_running)
    parts = [
        state_label,
        f"阶段 {format_phase_label(task)}",
        f"当前 {format_current_action(task)}",
        format_context_usage(session),
    ]
    if session.pending_confirmation is not None:
        parts.append(f"确认 {session.pending_confirmation.prompt or session.pending_confirmation.summary}")
    parts.extend(["Enter 发送", "Shift+Enter 换行"])
    return " | ".join(parts)


def format_context_usage(session: SessionState) -> str:
    limit = None
    if session.active_task is not None:
        limit = session.active_task.limits.max_estimated_tokens
    if limit is None or limit <= 0:
        return "上下文 --"
    _, usage = estimate_context_usage(session.messages, limit)
    if usage is None:
        return "上下文 --"
    percent = min(999, round(usage * 100))
    return f"上下文 {percent}%"


def format_status_label(task: TaskState, is_running: bool | None = None) -> str:
    if task.status == "waiting_confirmation":
        return "等待确认"
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


def build_display_line_starts(text: str, width: int) -> list[int]:
    width = max(1, width)
    starts = [0]
    column = 0
    for index, character in enumerate(text):
        if character == "\n":
            column = 0
            if index + 1 < len(text):
                starts.append(index + 1)
            continue

        character_width = max(0, get_cwidth(character))
        if column > 0 and column + character_width > width:
            starts.append(index)
            column = 0
        column += character_width
    return starts


def wrap_text_for_display(text: str, width: int) -> tuple[str, list[int]]:
    width = max(1, width)
    lines: list[str] = []
    starts: list[int] = [0]
    current: list[str] = []
    column = 0

    for index, character in enumerate(text):
        if character == "\n":
            lines.append("".join(current))
            current = []
            column = 0
            if index + 1 < len(text):
                starts.append(index + 1)
            continue

        character_width = max(0, get_cwidth(character))
        if column > 0 and column + character_width > width:
            lines.append("".join(current))
            starts.append(index)
            current = []
            column = 0
        current.append(character)
        column += character_width

    lines.append("".join(current))
    return "\n".join(lines), starts


def style_output_fragments(text: str) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []
    current_section = ""
    pending_section_title = False

    for line in text.splitlines(keepends=True):
        bare_line = line.rstrip("\n")
        if bare_line == SECTION_SEPARATOR:
            pending_section_title = True
        elif pending_section_title:
            current_section = bare_line
            pending_section_title = False

        style = "class:process" if current_section == "执行过程" else ""
        fragments.append((style, line))

    if not fragments:
        fragments.append(("", ""))
    return fragments


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
    def __init__(self, text: str, style: str = "") -> None:
        self._text = text
        self._rendered_text = text
        self.display_line_starts = build_line_starts(text)
        self.display_width: int | None = None
        self.scroll_top = 0
        self.follow_bottom = True
        self.buffer = ScrollPositionBuffer(self)
        self.document = ScrollPositionDocument(self)
        self.control = ScrollableTextControl(self)
        self.window = Window(
            content=self.control,
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
        return style_output_fragments(self._rendered_text)

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
        memory_manager = MemoryManager(resolved_options.cwd / ".manus-mini" / "memory.db")
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

        @key_bindings.add(Keys.ScrollDown)
        def _output_wheel_down(event) -> None:
            self.scroll_output(5)
            event.app.layout.focus(self.output)

        @key_bindings.add(Keys.ScrollUp)
        def _output_wheel_up(event) -> None:
            self.scroll_output(-5)
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
                "process": "bg:#172026 #8fa19c",
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
        self.app.run()


def main() -> None:
    PromptTui().run()
