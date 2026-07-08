from __future__ import annotations

import json
from bisect import bisect_right

from prompt_toolkit.utils import get_cwidth
from rich.console import Console
from rich.markdown import Markdown

from manus_mini.context import estimate_context_usage
from manus_mini.models import LoopLimits, Message, Observation, SessionState, TaskState, TraceEvent
from manus_mini.redaction import redact_sensitive_text


def format_messages(
    session: SessionState,
    omit_last_agent: bool = False,
) -> str:
    messages = [message for message in session.messages if not _is_internal_system_message(message)]
    if omit_last_agent and messages and messages[-1].role == "agent":
        messages = messages[:-1]
    if not messages:
        return "在下方输入你的指令，开始对话..."
    lines: list[str] = []
    for message in messages:
        lines.append(format_message_block(message))
    return "\n\n".join(lines)


def _is_internal_system_message(message: Message) -> bool:
    return message.role == "system" and message.content.startswith("长期记忆:")


SECTION_SEPARATOR = "────────────────────────────────────────"
USER_MESSAGE_BORDER = "────────────────────────────────────────"


def format_message_block(message: Message) -> str:
    speaker = "你" if message.role == "user" else "Agent"
    if message.role != "user":
        if message.role == "tool":
            return f"工具: {format_tool_message_summary(message.content)}"
        return f"{speaker}: {message.content}"

    lines = [f"┌─ {speaker} ───────────────────────────────────"]
    for line in message.content.splitlines() or [""]:
        lines.append(f"│  {line}")
    lines.append(f"└{USER_MESSAGE_BORDER}")
    return "\n".join(lines)


def format_tool_message_summary(content: str) -> str:
    paragraphs = [paragraph.strip() for paragraph in content.split("\n\n") if paragraph.strip()]
    summary = paragraphs[0] if paragraphs else "[工具已执行]"
    if _tool_message_has_file_content(paragraphs):
        label = _extract_tool_content_label(summary)
        if label:
            return f"[{label} 文件内容获取成功]"
        return "[文件内容获取成功]"
    return summary


def _tool_message_has_file_content(paragraphs: list[str]) -> bool:
    return any(
        paragraph.startswith("content:\n") and "[empty]" not in paragraph
        for paragraph in paragraphs[1:]
    )


def _extract_tool_content_label(summary: str) -> str:
    text = summary.strip()
    if text.startswith("read "):
        return text.removeprefix("read ").strip()
    if text.startswith("read_file "):
        return text.removeprefix("read_file ").strip()
    return ""


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
        return "当前步骤\n等待你的输入。"

    trace_events = task.trace_events
    if visible_trace_count is not None:
        trace_events = trace_events[: max(0, visible_trace_count)]

    sections = [
        format_task_overview(task),
        format_plan(task),
        format_llm_tool_rounds(task, trace_events, limit=None if full_history else 5),
    ]
    return "\n\n".join(section for section in sections if section)


def format_task_overview(task: TaskState) -> str:
    current = f"第 {max(task.step_count, 0)} 步" if task.step_count else "准备中"
    return "\n".join(
        [
            "步骤概览",
            f"- 目标：{task.goal}",
            f"- 阶段：{format_phase_label(task)}",
            f"- 动作：{format_current_action(task)}",
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
    if task.plan_reasoning_content:
        lines.append(f"  规划理由: {task.plan_reasoning_content}")
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
        lines.append("- 已返回")
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
    lines = [title]
    reasoning = format_llm_reasoning_summary(llm_event.data.get("reasoning_content"))
    if reasoning:
        lines.append(f"- 推理: {reasoning}")

    tool_calls = [call for call in llm_event.data.get("tool_calls", []) or [] if isinstance(call, dict)]
    if tool_calls:
        batch_groups = group_tool_calls_by_batch(tool_calls, following_events)
        lines.extend(format_tool_batch_sections(batch_groups, following_events, task, iteration))
    return "\n".join(lines)


def format_llm_reasoning_summary(value) -> str:
    text = redact_sensitive_text(str(value or "").strip())
    if not text:
        return ""
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(compact) <= 240:
        return compact
    return compact[:240] + "... [已截断]"


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
    call_index = 1
    for batch_index, batch_calls in batch_groups:
        lines.append(f"- 第 {batch_index} 批（{len(batch_calls)} 个工具）")
        batch_lines = format_tool_batch_lines(batch_index, iteration, batch_calls, events, task, start_index=call_index)
        call_index += len(batch_calls)
        lines.extend([f"  {line}" for line in batch_lines] or ["  - 等待工具返回。"])
    return lines


def format_tool_batch_lines(
    batch_index: int,
    iteration,
    tool_calls: list[dict],
    events: list[TraceEvent],
    task: TaskState,
    start_index: int = 1,
) -> list[str]:
    call_ids = {str(call.get("id", "unknown")) for call in tool_calls}
    prefixes = {
        str(call.get("id", "unknown")): f"{iteration}.{call_index}" if iteration else str(call_index)
        for call_index, call in enumerate(tool_calls, start=start_index)
    }
    default_prefix = next(iter(prefixes.values()), f"{iteration}.{batch_index}" if iteration else str(batch_index))
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
            prefix = prefixes.get(tool_call_id, default_prefix)
            diff_preview = str(event.data.get("diff_preview") or "").strip()
            if diff_preview:
                lines.append(f"- {prefix} {tool_name}({tool_call_id}) 变更预览:")
                lines.extend(f"  {line}" for line in diff_preview.splitlines())
                continue
            status = format_tool_return_status(event.data)
            summary = event.data.get("summary") or event.message
            line = f"- {prefix} {tool_name}({tool_call_id}) {status}: {redact_sensitive_text(str(summary))}"
            lines.append(line)
    if not lines:
        lines.append(f"- {default_prefix} 等待工具返回。")
    call_lines = []
    for call_index, call in enumerate(tool_calls, start=start_index):
        tool_call_id = str(call.get("id", "unknown"))
        name = str(call.get("name", "unknown"))
        args = format_inline_args(call.get("args", {}))
        prefix = prefixes.get(tool_call_id, f"{iteration}.{call_index}" if iteration else str(call_index))
        call_line = f"- {prefix} 调用 {name}({tool_call_id}) {args}".rstrip()
        call_lines.append(call_line)
        observation = observation_by_call.get(tool_call_id)
        if observation is not None and tool_call_id not in event_by_call_id:
            status = "成功" if observation.ok else "失败"
            result_line = f"- {prefix} {tool_call_id} {status}: {redact_sensitive_text(observation.summary)}"
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
        line = f"- {tool_name}({tool_call_id}) {status}: {redact_sensitive_text(str(summary))}"
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
        lines.append(line)
    return lines


def format_tool_return_status(data: dict) -> str:
    if "ok" not in data:
        return "已返回"
    return "成功" if data.get("ok") else "失败"


def format_latest_activity(task: TaskState) -> str:
    if not task.trace_events:
        return "等待执行"
    return format_event_summary(task.trace_events[-1])


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
        return "LLM：已返回"

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
        reason = event.data.get("reason")
        if reason:
            return f"反思：{event.message}（{reason}）"
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
        format_section("会话信息", format_session_run_info(session)),
        format_section("用户问题", format_user_question(session)),
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


def format_session_run_info(session: SessionState) -> str:
    task = session.active_task
    if task is None:
        return "- Run ID: 暂无"
    return f"- Run ID: {task.run_id}"


def format_welcome(
    limits: LoopLimits,
    llm_model: str | None = None,
    llm_configured: bool | None = None,
    llm_config_source: str | None = None,
) -> str:
    llm_lines: list[str] = ["模型配置"]
    if llm_configured:
        llm_lines.append(f"- 当前模型：{llm_model or '未指定'}")
        if llm_config_source:
            llm_lines.append(f"- 配置来源：{llm_config_source}")
    elif llm_configured is False:
        llm_lines.extend(
            [
                "- 未找到可用 LLM 配置。",
                "- 请设置环境变量，或在当前目录 `.env`、`~/.manus-mini/.env`、manus-mini 安装源码根目录 `.env` 中配置 LLM_PROVIDER / LLM_BASE_URL / LLM_API_KEY / LLM_MODEL。",
            ]
        )
    else:
        llm_lines.append("- 当前模型：启动后按配置加载")
    return "\n".join(
        [
            "欢迎使用 Manus Mini",
            "",
            "你可以在这里连续对话，让 Agent 读取项目、调用工具、生成报告或写入文件。",
            "",
            *llm_lines,
            "",
            "当前限制",
            f"- 工程循环上限：{limits.max_engineering_steps} 轮",
            f"- ReAct 循环上限：{limits.max_react_iterations} 轮",
            f"- Reflection 循环上限：{limits.max_reflection_rounds} 轮",
            f"- 工具重试上限：{limits.max_tool_retries} 次",
            f"- 工具执行时间：{format_tool_timeout_limit(limits.max_tool_timeout_seconds)}",
            "",
            "操作说明",
            "- Enter 发送消息",
            "- Shift+Enter 换行",
            "- 输入 `压缩上下文` 或 `/compact` 手动压缩上下文",
            "- 输入 `/save-context` 保存当前上下文快照",
            "- 输入 `/help` 查看全部指令",
            "- Tab 切换焦点区域",
            "- Ctrl-C 退出程序",
        ]
    )


def format_tool_timeout_limit(value: int | None) -> str:
    if value is None or value <= 0:
        return "不限制"
    return f"{value} 秒"


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
        return "就绪 | Enter 发送消息 | Shift+Enter 换行 | Ctrl-C 退出"

    state_label = format_status_label(task, is_running=is_running)

    return f"状态 {state_label} | {format_context_usage(session)} | Enter 发送消息 | Shift+Enter 换行"


def format_context_usage(session: SessionState) -> str:
    task = session.active_task
    if task is None:
        return "上下文 --"
    limit = task.model_context_limit or task.limits.max_estimated_tokens
    if limit <= 0:
        return "上下文 --"
    if task.last_prompt_tokens is not None:
        usage = task.last_prompt_tokens / limit
    else:
        _, usage = estimate_context_usage(session.messages, limit)
    if usage is None:
        return "上下文 --"
    percent = min(999.9, usage * 100)
    return f"上下文 {percent:.1f}%"


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
    current_section = "执行过程" if _looks_like_standalone_process_text(text) else ""
    pending_section_title = False
    in_process_diff = False
    in_reasoning = False

    for line in text.splitlines(keepends=True):
        bare_line = line.rstrip("\n")
        if bare_line == SECTION_SEPARATOR:
            pending_section_title = True
            in_process_diff = False
            in_reasoning = False
        elif pending_section_title:
            current_section = bare_line
            pending_section_title = False
            in_reasoning = False

        style = "class:process" if current_section == "执行过程" else ""
        if current_section == "执行过程":
            stripped_line = bare_line.strip()
            if stripped_line.startswith("- 推理:"):
                style = "class:process.reasoning"
                in_reasoning = True
            elif in_reasoning and stripped_line and not _is_process_control_line(stripped_line):
                style = "class:process.reasoning"
            elif stripped_line.endswith("变更预览:"):
                in_reasoning = False
                in_process_diff = True
            elif in_process_diff:
                if bare_line.startswith("  "):
                    style = _diff_line_style(stripped_line)
                elif bare_line:
                    in_process_diff = False
            else:
                in_reasoning = False
        fragments.append((style, line))

    if not fragments:
        fragments.append(("", ""))
    return fragments


def _diff_line_style(line: str) -> str:
    styles = {
        "diff": "bg:#172026 #d7dedb",
        "header": "bg:#1f2937 #cbd5e1",
        "add": "bg:#064e3b #d1fae5",
        "remove": "bg:#7f1d1d #fee2e2",
    }
    if line.startswith("+++") or line.startswith("---"):
        return styles["header"]
    if line.startswith("+"):
        return styles["add"]
    if line.startswith("-"):
        return styles["remove"]
    return styles["diff"]


def _is_process_control_line(line: str) -> bool:
    return line.startswith(
        (
            "- ",
            "LLM 回合",
            "工具调度",
            "步骤概览",
            "执行计划",
        )
    )


def _looks_like_standalone_process_text(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.startswith(("执行过程", "步骤概览", "执行计划", "LLM 回合", "工具调度"))
    return False


def style_confirmation_fragments(text: str) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []
    in_diff = False
    for line in text.splitlines(keepends=True):
        bare_line = line.rstrip("\n")
        if bare_line == "变更预览":
            in_diff = True
            fragments.append(("class:confirmation.body", line))
            continue
        if not in_diff:
            fragments.append(("class:confirmation.body", line))
            continue
        fragments.append((_diff_line_style(bare_line), line))
    if not fragments:
        fragments.append(("", ""))
    return fragments
