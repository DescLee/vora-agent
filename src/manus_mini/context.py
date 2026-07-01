from __future__ import annotations

from pathlib import Path
from typing import Literal, Sequence

from manus_mini.models import Artifact, CompressionSnapshot, ContextBundle, ContextSegment, MemoryItem, Message, Observation, SessionState
from manus_mini.redaction import redact_sensitive_text


class ContextIntegrityError(ValueError):
    pass


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
    ".manus-mini": "本地会话和缓存数据，通常不优先查看",
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
            "- src/manus_mini/runtime.py",
            "- src/manus_mini/react.py",
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


def compact_messages_with_snapshot(messages: Sequence[Message], token_budget: int) -> tuple[list[Message], CompressionSnapshot | None]:
    if not messages:
        return [], None

    segments = build_segments(messages)
    total_tokens = sum(segment.estimated_tokens for segment in segments)
    if total_tokens <= token_budget:
        return list(messages), None

    kept_reversed: list[ContextSegment] = []
    kept_tokens = 0
    for segment in reversed(segments):
        if kept_reversed and kept_tokens + segment.estimated_tokens > token_budget:
            break
        kept_reversed.append(segment)
        kept_tokens += segment.estimated_tokens

    kept_segments = list(reversed(kept_reversed))
    removed_count = len(segments) - len(kept_segments)
    if removed_count <= 0:
        return [message for segment in kept_segments for message in segment.messages], None

    covered_segments = segments[:removed_count]
    summary_text = _summarize_segments(covered_segments)
    summary = Message.system(summary_text)
    snapshot = CompressionSnapshot(
        covered_message_ids=[message.id for segment in covered_segments for message in segment.messages],
        covered_observation_ids=[],
        summary=summary_text,
        retained_facts=[line for line in summary_text.splitlines() if line.startswith("- ")],
    )
    compacted = [summary]
    for segment in kept_segments:
        compacted.extend(segment.messages)
    validate_tool_call_pairs(compacted)
    return compacted, snapshot


def _summarize_segments(segments: Sequence[ContextSegment]) -> str:
    parts = ["历史上下文摘要："]
    for segment in segments:
        if segment.kind == "tool_exchange":
            tool_ids = ", ".join(segment.messages[0].tool_call_ids)
            tool_summaries = [
                _preview(message.content)
                for message in segment.messages[1:]
                if message.role == "tool"
            ]
            parts.append(f"- 工具交换 {tool_ids}: {'; '.join(tool_summaries)}")
            continue

        message = segment.messages[0]
        speaker = "用户" if message.role == "user" else "Agent" if message.role == "agent" else message.role
        parts.append(f"- {speaker}: {_preview(message.content)}")
    return "\n".join(parts)


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
        preferred = {"manus_mini": 0}
        return sorted(children, key=lambda item: (preferred.get(item.name, 100), not item.is_dir(), item.name.lower()))
    if directory.name == "manus_mini":
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
        if relative == "src/manus_mini":
            return "主应用包，包含运行链路和工具逻辑"
        if relative == "tests":
            return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS["tests"]
        if relative == "docs":
            return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS["docs"]
        if relative == ".manus-mini":
            return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS[".manus-mini"]
        if relative == "runs":
            return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS["runs"]
        if relative == "outputs":
            return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS["outputs"]
        return "项目目录"

    if relative in PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS:
        return PROJECT_OVERVIEW_TOP_LEVEL_DESCRIPTIONS[relative]
    if relative.startswith("src/manus_mini/") and name in PROJECT_OVERVIEW_SRC_DESCRIPTIONS:
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
    if name in {".git", ".idea", ".vscode", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "node_modules", "dist", "build", "outputs", "runs"}:
        return True
    return name.startswith(".") and name not in {".env.example", ".gitignore"}


__all__ = [
    "ContextIntegrityError",
    "build_project_code_overview",
    "build_segments",
    "build_context_bundle",
    "compact_messages",
    "compact_messages_with_snapshot",
    "estimate_context_usage",
    "estimate_session_context_usage",
    "estimate_message_tokens",
    "estimate_tokens",
    "should_include_project_code_overview",
    "validate_tool_call_pairs",
]
