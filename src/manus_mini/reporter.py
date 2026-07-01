from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from manus_mini.models import TaskState
from manus_mini.redaction import redact_sensitive_text


class Reporter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def write_markdown(self, filename: str, content: str) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self._available_path(filename)
        path.write_text(content, encoding="utf-8")
        return path

    def write_task_report(self, filename: str, task: TaskState, user_input: str) -> Path:
        report = self.write_markdown(filename, render_task_report(task, user_input))
        self.write_run_summary(task, user_input)
        return report

    def write_run_summary(self, task: TaskState, user_input: str) -> Path:
        session_id = task.session_id or "unknown-session"
        run_dir = self.output_dir.parent / "runs" / f"{session_id}-{task.run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self._available_run_summary_path(run_dir, f"summary-{timestamp}.md")
        path.write_text(render_run_summary(task, user_input), encoding="utf-8")
        return path

    def _available_path(self, filename: str) -> Path:
        candidate = self.output_dir / filename
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        for index in range(1, 10_000):
            next_candidate = self.output_dir / f"{stem}-{index}{suffix}"
            if not next_candidate.exists():
                return next_candidate
        raise RuntimeError(f"no available output filename for {filename}")

    def _available_run_summary_path(self, run_dir: Path, filename: str) -> Path:
        candidate = run_dir / filename
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        for index in range(1, 10_000):
            next_candidate = run_dir / f"{stem}-{index}{suffix}"
            if not next_candidate.exists():
                return next_candidate
        raise RuntimeError(f"no available run summary filename for {filename}")


def render_task_report(task: TaskState, user_input: str, chunk_size: int = 4000) -> str:
    safe_user_input = redact_sensitive_text(user_input)
    sections = [
        "# Manus Mini Run",
        "",
        "## 1. 用户输入",
        "",
        *markdown_chunks(safe_user_input, chunk_size=chunk_size),
        "",
        "## 2. 执行过程",
        "",
    ]

    if task.trace_events:
        for index, event in enumerate(task.trace_events, start=1):
            sections.extend(
                [
                    f"### 2.{index} [{event.phase}] {event.message}",
                    "",
                    *markdown_chunks(redact_sensitive_text(format_event_data(event.data)), chunk_size=chunk_size),
                    "",
                ]
            )
    else:
        sections.extend(["暂无执行过程。", ""])

    sections.extend(["## 3. 工具观察", ""])
    if task.observations:
        for index, observation in enumerate(task.observations, start=1):
            title = f"### 3.{index} {observation.tool_call_id or 'unknown'}"
            status = "成功" if observation.ok else "失败"
            sections.extend(
                [
                    title,
                    "",
                    f"- 状态：{status}",
                    f"- 摘要：{redact_sensitive_text(observation.summary)}",
                    "",
                    *markdown_chunks(redact_sensitive_text(observation.content or "无内容。"), chunk_size=chunk_size),
                    "",
                ]
            )
    else:
        sections.extend(["暂无工具观察。", ""])

    sections.extend(
        [
            "## 4. 最终产物",
            "",
            *markdown_chunks(redact_sensitive_text(task.result or "暂无最终产物。"), chunk_size=chunk_size),
            "",
        ]
    )
    return "\n".join(sections)


def render_run_summary(task: TaskState, user_input: str) -> str:
    return "\n".join(
        [
            "# Manus Mini Run Summary",
            "",
            f"- goal: {redact_sensitive_text(user_input)}",
            f"- status: {task.status}",
            f"- steps: {task.step_count}",
            f"- observations: {len(task.observations)}",
            f"- artifacts: {len(task.artifacts)}",
            "",
            "## Result",
            "",
            redact_sensitive_text(task.result or "暂无结果。"),
        ]
    )


def markdown_chunks(content: str, chunk_size: int = 4000) -> list[str]:
    text = content if content else "无内容。"
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    for index, start in enumerate(range(0, len(text), chunk_size), start=1):
        chunks.extend([f"#### chunk {index}", "", text[start : start + chunk_size], ""])
    return chunks


def format_event_data(data: dict[str, Any]) -> str:
    if not data:
        return "无数据。"
    lines = []
    for key, value in data.items():
        lines.append(f"- `{key}`: `{redact_sensitive_text(repr(value))}`")
    return "\n".join(lines)
