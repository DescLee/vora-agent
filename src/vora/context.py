from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, Field
from typing import Any, Literal, Protocol, Sequence

from vora.models import Artifact, CompressionSnapshot, ContextBundle, ContextSegment, MemoryItem, Message, Observation, SessionState
from vora.redaction import redact_sensitive_text


class ContextIntegrityError(ValueError):
    pass


class ContextSummaryLLM(Protocol):
    def complete_with_tools(self, messages: list[Any], tool_names: list[str]) -> Any:
        ...


COMPRESSION_TOOL_THRESHOLD = 0.50
COMPRESSION_HISTORY_THRESHOLD = 0.70
COMPRESSION_FORCE_THRESHOLD = 0.90
MAX_TOOL_MESSAGE_CHARS = 800
RECENT_HISTORY_MESSAGE_COUNT = 4


class CompressionPipelineResult(BaseModel):
    messages: list[Message]
    snapshots: list[CompressionSnapshot] = Field(default_factory=list)
    applied_strategies: list[str] = Field(default_factory=list)
    before_tokens: int
    after_tokens: int
    before_usage: float | None = None
    after_usage: float | None = None
    trigger_stage: str = ""


PROJECT_OVERVIEW_HINT = "project_code_overview"
PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS = {
    "README.md": "项目说明、安装方式和使用入口",
    "pyproject.toml": "Python 项目配置、依赖和脚本入口",
    "setup.py": "传统 Python 打包入口",
    "setup.cfg": "传统 Python 配置入口",
    "package.json": "Node/前端项目配置和脚本入口",
    "pnpm-lock.yaml": "前端依赖锁文件",
    "uv.lock": "Python 依赖锁文件",
    "requirements.txt": "Python 依赖清单",
    ".env.example": "环境变量示例",
    ".gitignore": "仓库忽略规则",
    "src": "核心实现代码",
    "tests": "自动化测试",
    "docs": "设计文档、问题记录和优化说明",
    "scripts": "辅助脚本",
    ".vora": "本地会话和缓存数据，通常不优先查看",
    "logs": "运行日志和调试记录，通常不优先查看",
    "runs": "运行日志和执行产物，通常不优先查看",
    "outputs": "产物输出目录，通常不优先查看",
}
PROJECT_OVERVIEW_SRC_DESCRIPTIONS = {
    "runtime.py": "运行编排、外层循环和中断兜底",
    "react.py": "ReAct 循环、工具调度和上下文拼装",
    "reflection.py": "反思与重规划决策",
    "planner.py": "初始计划生成",
    "llm.py": "LLM 适配、请求和工具 schema",
    "prompt_tui.py": "终端界面和过程展示",
    "session.py": "会话处理、指令和保存恢复",
    "session_store.py": "会话持久化",
    "context.py": "上下文压缩和拼装",
    "memory.py": "长期记忆",
    "reporter.py": "运行报告输出",
    "logging.py": "JSONL 运行日志",
    "redaction.py": "敏感信息脱敏",
    "models.py": "核心数据模型和限制参数",
}
PROJECT_OVERVIEW_TEST_HINTS = {
    "test_runtime.py": "运行链路回归测试",
    "test_prompt_tui.py": "TUI 展示和交互测试",
    "test_context.py": "上下文拼装和压缩测试",
    "test_tools.py": "文件工具和安全边界测试",
    "test_llm.py": "LLM 适配和工具调用格式测试",
}


def estimate_tokens(text: str, kind: Literal["zh", "en", "code", "mixed"]) -> int:
    if kind == "zh":
        return int(len(text) * 1.2)
    if kind == "en":
        return int(len(text.split()) * 1.3)
    if kind == "code":
        return max(1, len(text) // 3)
    return max(1, len(text) // 2)


def validate_tool_call_pairs(messages: Sequence[Message]) -> None:
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]

        if message.role == "agent" and message.tool_call_ids:
            expected_ids = list(message.tool_call_ids)
            if len(expected_ids) != len(set(expected_ids)):
                raise ContextIntegrityError(
                    "context tool_call_id integrity failed: duplicate tool_call_ids in agent message"
                )

            index += 1
            while expected_ids:
                if index >= total:
                    raise ContextIntegrityError(
                        "context tool_call_id integrity failed: missing tool result for assistant tool_calls"
                    )

                next_message = messages[index]
                if next_message.role != "tool":
                    raise ContextIntegrityError(
                        "context tool_call_id integrity failed: tool_exchange must stay contiguous"
                    )
                if next_message.tool_call_id not in expected_ids:
                    raise ContextIntegrityError(
                        "context tool_call_id integrity failed: orphan tool_call_id "
                        f"{next_message.tool_call_id!r}"
                    )

                expected_ids.remove(next_message.tool_call_id)
                index += 1
            continue

        if message.role == "tool":
            raise ContextIntegrityError(
                "context tool_call_id integrity failed: orphan tool_call_id "
                f"{message.tool_call_id!r}"
            )

        index += 1


def complete_interrupted_tool_messages(messages: list[Message]) -> int:
    inserted = 0
    repaired: list[Message] = []
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]
        if message.role == "agent" and message.tool_call_ids:
            repaired.append(message)
            expected_ids = list(message.tool_call_ids)
            index += 1
            while expected_ids and index < total:
                next_message = messages[index]
                if next_message.role != "tool":
                    break
                if next_message.tool_call_id not in expected_ids:
                    break
                repaired.append(next_message)
                expected_ids.remove(next_message.tool_call_id)
                index += 1
            for tool_call_id in expected_ids:
                repaired.append(_interrupted_tool_message(tool_call_id))
                inserted += 1
            continue

        if message.role == "tool":
            repaired.append(
                Message.system(
                    "orphan tool result converted after interruption"
                    f" ({message.tool_call_id or 'unknown'}):\n{message.content}"
                )
            )
            index += 1
            continue

        repaired.append(message)
        index += 1

    messages[:] = repaired
    return inserted


def _interrupted_tool_message(tool_call_id: str) -> Message:
    return Message.tool(
        "tool execution interrupted by user\n\nerror_code: USER_CANCELLED",
        tool_call_id=tool_call_id,
    )


def build_segments(messages: Sequence[Message]) -> list[ContextSegment]:
    validate_tool_call_pairs(messages)

    segments: list[ContextSegment] = []
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]

        if message.role == "agent" and message.tool_call_ids:
            group = [message]
            expected_ids = list(message.tool_call_ids)
            index += 1
            while expected_ids:
                next_message = messages[index]
                group.append(next_message)
                assert next_message.tool_call_id is not None
                expected_ids.remove(next_message.tool_call_id)
                index += 1

            segments.append(
                ContextSegment(
                    id=group[0].id,
                    kind="tool_exchange",
                    messages=group,
                    estimated_tokens=sum(estimate_tokens(item.content, "mixed") for item in group),
                    priority=50,
                )
            )
            continue

        segments.append(
            ContextSegment(
                id=message.id,
                kind="plain_message",
                messages=[message],
                estimated_tokens=estimate_tokens(message.content, "mixed"),
                priority=100,
            )
        )
        index += 1

    return segments


def compact_messages(messages: Sequence[Message], token_budget: int) -> list[Message]:
    compacted, _ = compact_messages_with_snapshot(messages, token_budget)
    return compacted


def estimate_message_tokens(messages: Sequence[Message]) -> int:
    return sum(max(1, len(message.content) // 2) for message in messages)


def estimate_context_usage(messages: Sequence[Message], token_limit: int | None) -> tuple[int, float | None]:
    estimated_tokens = estimate_message_tokens(messages)
    if token_limit is None or token_limit <= 0:
        return estimated_tokens, None
    return estimated_tokens, estimated_tokens / token_limit


def estimate_session_context_usage(session: SessionState, token_limit: int | None) -> tuple[int, float | None]:
    return estimate_context_usage(session.messages, token_limit)


def run_context_compression_pipeline(
    messages: Sequence[Message],
    token_limit: int | None,
    trigger_stage: str,
    llm: ContextSummaryLLM | None = None,
) -> CompressionPipelineResult:
    before_tokens, before_usage = estimate_context_usage(messages, token_limit)
    current = list(messages)
    pending_tail: list[Message] = []
    if current and current[-1].role == "agent" and current[-1].tool_call_ids:
        pending_tail = [current[-1]]
        current = current[:-1]
    snapshots: list[CompressionSnapshot] = []
    applied: list[str] = []
    if token_limit is None or token_limit <= 0 or before_usage is None or before_usage <= COMPRESSION_TOOL_THRESHOLD:
        return CompressionPipelineResult(
            messages=[*current, *pending_tail],
            snapshots=snapshots,
            applied_strategies=applied,
            before_tokens=before_tokens,
            after_tokens=before_tokens,
            before_usage=before_usage,
            after_usage=before_usage,
            trigger_stage=trigger_stage,
        )

    current, tool_snapshot = compact_tool_messages(current, trigger_stage=trigger_stage)
    if tool_snapshot is not None:
        snapshots.append(tool_snapshot)
        applied.append("tool_message")

    _, current_usage = estimate_context_usage([*current, *pending_tail], token_limit)
    if current_usage is not None and current_usage > COMPRESSION_TOOL_THRESHOLD:
        history_budget = max(1, int(token_limit * COMPRESSION_TOOL_THRESHOLD))
        compacted, snapshot = compact_middle_history_with_snapshot(current, token_budget=history_budget, llm=llm)
        if snapshot is not None:
            snapshot.metadata["strategy"] = "history_summary"
            snapshot.metadata["trigger_stage"] = trigger_stage
            snapshots.append(snapshot)
            applied.append("history_summary")
            current = compacted

    _, current_usage = estimate_context_usage([*current, *pending_tail], token_limit)
    if before_usage > COMPRESSION_FORCE_THRESHOLD or (current_usage is not None and current_usage > COMPRESSION_HISTORY_THRESHOLD):
        force_budget = max(1, int(token_limit * COMPRESSION_TOOL_THRESHOLD))
        current, snapshot = force_truncate_history(current, token_budget=force_budget, llm=llm, trigger_stage=trigger_stage)
        if snapshot is not None:
            snapshots.append(snapshot)
            applied.append("force_truncate")

    current = [*current, *pending_tail]
    after_tokens, after_usage = estimate_context_usage(current, token_limit)
    return CompressionPipelineResult(
        messages=current,
        snapshots=snapshots,
        applied_strategies=applied,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        before_usage=before_usage,
        after_usage=after_usage,
        trigger_stage=trigger_stage,
    )


def compact_tool_messages(
    messages: Sequence[Message],
    trigger_stage: str = "",
    max_chars: int = MAX_TOOL_MESSAGE_CHARS,
) -> tuple[list[Message], CompressionSnapshot | None]:
    compacted: list[Message] = []
    covered_ids: list[str] = []
    compressed_chars = 0
    retained_facts: list[str] = []
    for message in messages:
        if message.role != "tool" or len(message.content) <= max_chars:
            compacted.append(message)
            continue
        head_chars = max(1, max_chars // 2)
        tail_chars = max(1, max_chars - head_chars)
        omitted = len(message.content) - head_chars - tail_chars
        content = (
            message.content[:head_chars]
            + f"\n... [中间已压缩 {omitted} 个字符] ...\n"
            + message.content[-tail_chars:]
        )
        compacted_message = message.model_copy(update={"content": content})
        compacted.append(compacted_message)
        covered_ids.append(message.id)
        compressed_chars += omitted
        retained_facts.append(f"- 工具消息 {message.tool_call_id or message.id} 保留首尾，压缩 {omitted} 个字符。")

    if not covered_ids:
        return compacted, None
    snapshot = CompressionSnapshot(
        covered_message_ids=covered_ids,
        covered_observation_ids=[],
        summary=f"工具消息压缩：共压缩 {compressed_chars} 个字符。",
        retained_facts=retained_facts,
    )
    snapshot.metadata.update(
        {
            "strategy": "tool_message",
            "trigger_stage": trigger_stage,
            "compressed_chars": compressed_chars,
            "summary_source": "rule",
        }
    )
    validate_tool_call_pairs(compacted)
    return compacted, snapshot


def force_truncate_history(
    messages: Sequence[Message],
    token_budget: int,
    llm: ContextSummaryLLM | None = None,
    trigger_stage: str = "",
) -> tuple[list[Message], CompressionSnapshot | None]:
    segments = build_segments(messages)
    if not segments:
        return list(messages), None

    kept_indexes = _force_keep_segment_indexes(segments)
    compacted = [message for index, segment in enumerate(segments) if index in kept_indexes for message in segment.messages]
    covered = [segment for index, segment in enumerate(segments) if index not in kept_indexes]
    while estimate_message_tokens(compacted) > token_budget and len(kept_indexes) > 1:
        removable = [index for index in sorted(kept_indexes) if not _is_system_segment(segments[index]) and index != max(kept_indexes)]
        if not removable:
            break
        index = removable[-1]
        kept_indexes.remove(index)
        covered.append(segments[index])
        compacted = [message for idx, segment in enumerate(segments) if idx in kept_indexes for message in segment.messages]

    compacted = _rewrite_latest_user_if_needed(compacted, token_budget=token_budget, llm=llm)
    validate_tool_call_pairs(compacted)
    covered_message_ids = [message.id for segment in covered for message in segment.messages]
    if not covered_message_ids and [message.id for message in compacted] == [message.id for message in messages]:
        return compacted, None
    summary = f"历史上下文强制截断：移除 {sum(len(segment.messages) for segment in covered)} 条低相关历史消息。"
    snapshot = CompressionSnapshot(
        covered_message_ids=covered_message_ids,
        covered_observation_ids=[],
        summary=summary,
        retained_facts=[summary],
    )
    snapshot.metadata.update(
        {
            "strategy": "force_truncate",
            "trigger_stage": trigger_stage,
            "summary_source": "rule",
        }
    )
    return compacted, snapshot


def _force_keep_segment_indexes(segments: Sequence[ContextSegment]) -> set[int]:
    keep: set[int] = set()
    for index, segment in enumerate(segments):
        if _is_system_segment(segment):
            keep.add(index)
    for index, segment in enumerate(segments):
        if any(message.role == "user" for message in segment.messages):
            keep.add(index)
            break
    for index in range(len(segments) - 1, -1, -1):
        if any(message.role == "user" for message in segments[index].messages):
            keep.add(index)
            break
    return keep


def _is_system_segment(segment: ContextSegment) -> bool:
    return bool(segment.messages) and segment.messages[0].role == "system"


def _rewrite_latest_user_if_needed(
    messages: list[Message],
    token_budget: int,
    llm: ContextSummaryLLM | None = None,
) -> list[Message]:
    if estimate_message_tokens(messages) <= token_budget:
        return messages
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role != "user" or len(message.content) <= MAX_TOOL_MESSAGE_CHARS:
            continue
        rewritten = _rewrite_user_message_with_llm(message.content, llm=llm)
        updated = list(messages)
        updated[index] = message.model_copy(update={"content": rewritten})
        return updated
    return messages


def _rewrite_user_message_with_llm(content: str, llm: ContextSummaryLLM | None) -> str:
    fallback = _preview(content, limit=MAX_TOOL_MESSAGE_CHARS)
    if llm is None:
        return fallback
    try:
        result = llm.complete_with_tools(
            [
                Message.system(
                    "请把这条过长的用户消息改写得更短，保留原始目标、约束和必须执行的要求，只输出改写后的用户消息。"
                ),
                Message.user(content),
            ],
            [],
        )
    except Exception:  # noqa: BLE001
        return fallback
    rewritten = str(getattr(result, "content", "") or "").strip()
    return redact_sensitive_text(rewritten) if rewritten else fallback


def should_include_project_code_overview(text: str) -> bool:
    normalized = text.strip().lower()
    if not normalized:
        return False
    keywords = (
        "项目",
        "代码",
        "源码",
        "仓库",
        "工程",
        "目录",
        "结构",
        "实现",
        "分析",
        "优化",
        "设计",
        "修改",
        "修复",
        "新增",
        "重构",
        "阅读",
        "查看",
        "理解",
        "说明",
        "总结",
    )
    return any(keyword in normalized for keyword in keywords)


def build_project_code_overview(workspace: Path, max_depth: int = 2) -> str:
    root = workspace.expanduser().resolve()
    if not root.exists():
        return "\n".join(
            [
                "项目代码目录结构",
                "- 当前工作目录不存在或不可访问。",
            ]
        )

    lines = [
        "项目代码目录结构",
        "- 这是一份只读结构摘要，先看它，再决定是否调用 list_files 或 read_file。",
        "- 目录后面的说明是用途提示，不是文件内容。",
        "",
    ]
    for child in _project_overview_top_level_entries(root):
        lines.extend(_format_overview_entry(child, root, depth=0, max_depth=max_depth))
    lines.extend(
        [
            "",
            "建议优先查看",
            "- README.md",
            "- pyproject.toml",
            "- src/vora/runtime.py",
            "- src/vora/react.py",
            "- docs/",
            "- tests/",
        ]
    )
    return "\n".join(lines).rstrip()


def build_context_bundle(
    current_user_message: Message,
    recent_messages: Sequence[Message],
    relevant_memories: Sequence[MemoryItem] | None = None,
    compression_summaries: Sequence[CompressionSnapshot] | None = None,
    active_artifacts: Sequence[Artifact] | None = None,
    recent_observations: Sequence[Observation] | None = None,
) -> ContextBundle:
    return ContextBundle(
        current_user_message=current_user_message,
        recent_messages=list(recent_messages),
        relevant_memories=list(relevant_memories or []),
        compression_summaries=list(compression_summaries or []),
        active_artifacts=list(active_artifacts or []),
        recent_observations=list(recent_observations or []),
    )


def compact_messages_with_snapshot(
    messages: Sequence[Message],
    token_budget: int,
    llm: ContextSummaryLLM | None = None,
) -> tuple[list[Message], CompressionSnapshot | None]:
    if not messages:
        return [], None

    segments = build_segments(messages)
    total_tokens = sum(segment.estimated_tokens for segment in segments)
    if total_tokens <= token_budget:
        return list(messages), None

    summary_token_budget = max(16, min(256, token_budget // 5))
    keep_token_budget = max(1, token_budget - summary_token_budget)
    kept_reversed: list[ContextSegment] = []
    kept_tokens = 0
    for segment in reversed(segments):
        if kept_reversed and kept_tokens + segment.estimated_tokens > keep_token_budget:
            break
        kept_reversed.append(segment)
        kept_tokens += segment.estimated_tokens

    kept_segments = list(reversed(kept_reversed))
    removed_count = len(segments) - len(kept_segments)
    if removed_count <= 0:
        return [message for segment in kept_segments for message in segment.messages], None

    covered_segments = segments[:removed_count]
    summary_text, summary_source = _summarize_segments_with_llm(covered_segments, llm=llm, token_budget=summary_token_budget)
    summary = Message.system(summary_text)
    snapshot = CompressionSnapshot(
        covered_message_ids=[message.id for segment in covered_segments for message in segment.messages],
        covered_observation_ids=[],
        summary=summary_text,
        retained_facts=[line for line in summary_text.splitlines() if line.startswith("- ")],
    )
    snapshot.metadata["summary_source"] = summary_source
    compacted = [summary]
    for segment in kept_segments:
        compacted.extend(segment.messages)
    validate_tool_call_pairs(compacted)
    return compacted, snapshot


def compact_middle_history_with_snapshot(
    messages: Sequence[Message],
    token_budget: int,
    llm: ContextSummaryLLM | None = None,
) -> tuple[list[Message], CompressionSnapshot | None]:
    segments = build_segments(messages)
    if len(segments) <= 3:
        return compact_messages_with_snapshot(messages, token_budget=token_budget, llm=llm)

    keep_indexes = _middle_summary_keep_indexes(segments)
    covered_segments = [segment for index, segment in enumerate(segments) if index not in keep_indexes]
    if not covered_segments:
        return list(messages), None

    summary_text, summary_source = _summarize_segments_with_llm(
        covered_segments,
        llm=llm,
        token_budget=max(16, min(256, token_budget // 5)),
    )
    summary = Message.system(summary_text)
    compacted: list[Message] = []
    inserted_summary = False
    for index, segment in enumerate(segments):
        if index in keep_indexes:
            compacted.extend(segment.messages)
            continue
        if not inserted_summary:
            compacted.append(summary)
            inserted_summary = True
    if not inserted_summary:
        compacted.append(summary)
    snapshot = CompressionSnapshot(
        covered_message_ids=[message.id for segment in covered_segments for message in segment.messages],
        covered_observation_ids=[],
        summary=summary_text,
        retained_facts=[line for line in summary_text.splitlines() if line.startswith("- ")],
    )
    snapshot.metadata["summary_source"] = summary_source
    validate_tool_call_pairs(compacted)
    return compacted, snapshot


def _middle_summary_keep_indexes(segments: Sequence[ContextSegment]) -> set[int]:
    keep: set[int] = set()
    for index, segment in enumerate(segments):
        if _is_system_segment(segment):
            keep.add(index)
    for index, segment in enumerate(segments):
        if any(message.role == "user" for message in segment.messages):
            keep.add(index)
            break
    recent_start = max(0, len(segments) - RECENT_HISTORY_MESSAGE_COUNT)
    keep.update(range(recent_start, len(segments)))
    return keep


def _summarize_segments_with_llm(
    segments: Sequence[ContextSegment],
    llm: ContextSummaryLLM | None,
    token_budget: int | None = None,
) -> tuple[str, str]:
    fallback = _summarize_segments(segments, token_budget=token_budget)
    if llm is None:
        return fallback, "rule"
    try:
        result = llm.complete_with_tools(_build_context_summary_prompt(segments, fallback), [])
    except Exception:  # noqa: BLE001
        return fallback, "rule_fallback"
    content = str(getattr(result, "content", "") or "").strip()
    if not content:
        return fallback, "rule_fallback"
    return _normalize_summary_text(redact_sensitive_text(content)), "llm"


def _build_context_summary_prompt(segments: Sequence[ContextSegment], fallback: str) -> list[Message]:
    source_lines = []
    for segment in segments:
        for message in segment.messages:
            if message.role == "tool":
                source_lines.append(f"tool({message.tool_call_id or 'unknown'}): {_preview(message.content, limit=500)}")
            else:
                source_lines.append(f"{message.role}: {_preview(message.content, limit=500)}")
    source_text = "\n".join(source_lines)
    return [
        Message.system(
            "\n".join(
                [
                    "你是上下文压缩器。请把较早对话压缩成短摘要，用于后续 Agent 继续任务。",
                    "要求：",
                    "- 只输出中文摘要，不要输出解释。",
                    "- 必须保留用户目标、明确约束、已做决策、文件/产物引用、工具观察中的关键事实。",
                    "- 不要保留密钥、token、password、secret 等敏感信息。",
                    "- 输出格式以“历史上下文摘要：”开头，后续使用短 bullet。",
                    "",
                    "规则压缩候选摘要：",
                    fallback,
                    "",
                    "待压缩原始片段：",
                    source_text,
                ]
            )
        )
    ]


def _normalize_summary_text(content: str) -> str:
    if content.startswith("历史上下文摘要："):
        return content
    return "历史上下文摘要：\n" + content


def _summarize_segments(segments: Sequence[ContextSegment], token_budget: int | None = None) -> str:
    parts = ["历史上下文摘要："]
    max_chars = max(160, token_budget * 2) if token_budget is not None and token_budget > 0 else None
    omitted_count = 0
    for index, segment in enumerate(segments):
        if segment.kind == "tool_exchange":
            tool_ids = ", ".join(segment.messages[0].tool_call_ids)
            tool_summaries = [
                _preview(message.content)
                for message in segment.messages[1:]
                if message.role == "tool"
            ]
            line = f"- 工具交换 {tool_ids}: {'; '.join(tool_summaries)}"
            if max_chars is not None and _joined_length(parts, line) > max_chars:
                omitted_count = len(segments) - index
                break
            parts.append(line)
            continue

        message = segment.messages[0]
        speaker = "用户" if message.role == "user" else "Agent" if message.role == "agent" else message.role
        line = f"- {speaker}: {_preview(message.content)}"
        if max_chars is not None and _joined_length(parts, line) > max_chars:
            omitted_count = len(segments) - index
            break
        parts.append(line)
    if omitted_count:
        parts.append(f"- 另有 {omitted_count} 段较早上下文已省略。")
    return "\n".join(parts)


def _joined_length(parts: Sequence[str], next_line: str) -> int:
    return len("\n".join([*parts, next_line]))


def _preview(content: str, limit: int = 160) -> str:
    compact = " ".join(redact_sensitive_text(content).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _project_overview_top_level_entries(root: Path) -> list[Path]:
    entries = [entry for entry in sorted(root.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())) if not _is_noise_entry(entry)]
    preferred_order = {
        "README.md": 0,
        "pyproject.toml": 1,
        "package.json": 2,
        "pnpm-lock.yaml": 3,
        "uv.lock": 4,
        "requirements.txt": 5,
        "setup.py": 6,
        "setup.cfg": 7,
        ".env.example": 8,
        "src": 9,
        "tests": 10,
        "docs": 11,
        "scripts": 12,
    }
    return sorted(entries, key=lambda item: (preferred_order.get(item.name, 100), not item.is_dir(), item.name.lower()))


def _format_overview_entry(path: Path, root: Path, depth: int, max_depth: int) -> list[str]:
    relative = path.relative_to(root).as_posix()
    name = path.name + ("/" if path.is_dir() else "")
    description = _describe_overview_path(relative, path.is_dir())
    indent = "  " * depth
    lines = [f"{indent}- {name}：{description}"]
    if not path.is_dir() or depth >= max_depth:
        return lines

    children = _project_overview_children(path)
    for child in children:
        lines.extend(_format_overview_entry(child, root, depth + 1, max_depth))
    return lines


def _project_overview_children(directory: Path) -> list[Path]:
    children = [entry for entry in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())) if not _is_noise_entry(entry)]
    if directory.name == "src":
        preferred = {"vora": 0}
        return sorted(children, key=lambda item: (preferred.get(item.name, 100), not item.is_dir(), item.name.lower()))
    if directory.name == "vora":
        preferred = {
            "runtime.py": 0,
            "react.py": 1,
            "reflection.py": 2,
            "planner.py": 3,
            "llm.py": 4,
            "prompt_tui.py": 5,
            "session.py": 6,
            "session_store.py": 7,
            "context.py": 8,
            "memory.py": 9,
            "reporter.py": 10,
            "logging.py": 11,
            "redaction.py": 12,
            "models.py": 13,
        }
        return sorted(children, key=lambda item: (preferred.get(item.name, 100), not item.is_dir(), item.name.lower()))
    if directory.name == "docs":
        return children[:12]
    if directory.name == "tests":
        return children[:12]
    return children[:12]


def _describe_overview_path(relative: str, is_dir: bool) -> str:
    name = Path(relative).name
    if is_dir:
        if relative == "src":
            return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS["src"]
        if relative == "src/vora":
            return "主应用包，包含运行链路和工具逻辑"
        if relative == "tests":
            return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS["tests"]
        if relative == "docs":
            return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS["docs"]
        if relative == ".vora":
            return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS[".vora"]
        if relative == "runs":
            return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS["runs"]
        if relative == "outputs":
            return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS["outputs"]
        return "项目目录"

    if relative in PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS:
        return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS[relative]
    if relative.startswith("src/vora/") and name in PROJECT_OVERVIEW_SRC_DESCRIPTIONS:
        return PROJECT_OVERVIEW_SRC_DESCRIPTIONS[name]
    if relative.startswith("tests/") and name in PROJECT_OVERVIEW_TEST_HINTS:
        return PROJECT_OVERVIEW_TEST_HINTS[name]
    if relative.startswith("docs/") and relative.endswith(".md"):
        return "设计、问题记录或产品文档"
    if name.endswith(".py"):
        return "Python 源码文件"
    if name.endswith(".md"):
        return "Markdown 文档"
    return "项目文件"


def _is_noise_entry(path: Path) -> bool:
    name = path.name
    if name in {".git", ".idea", ".vscode", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "node_modules", "dist", "build", "logs", "outputs", "runs"}:
        return True
    return name.startswith(".") and name not in {".env.example", ".gitignore"}


__all__ = [
    "ContextIntegrityError",
    "build_project_code_overview",
    "build_segments",
    "build_context_bundle",
    "compact_messages",
    "compact_messages_with_snapshot",
    "complete_interrupted_tool_messages",
    "estimate_context_usage",
    "estimate_session_context_usage",
    "estimate_message_tokens",
    "estimate_tokens",
    "should_include_project_code_overview",
    "validate_tool_call_pairs",
]
