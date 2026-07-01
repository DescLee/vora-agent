from __future__ import annotations

from datetime import datetime
from pathlib import Path

from manus_mini.context import compact_messages_with_snapshot, estimate_message_tokens
from manus_mini.models import Message, SessionState
from manus_mini.memory import MemoryManager
from manus_mini.runtime import AgentRuntime
from manus_mini.session_store import SessionStore

CONFIRMATION_WORDS = {"y", "yes", "confirm", "confirmed", "确认", "同意", "好", "继续"}
DENIAL_WORDS = {"n", "no", "cancel", "cancelled", "取消", "否", "不要"}
COMPACT_CONTEXT_COMMANDS = {"压缩上下文", "手动压缩上下文", "/compact", "compact context"}
SAVE_CONTEXT_COMMANDS = {"/save-context", "save context", "保存上下文"}
HELP_COMMANDS = {"/help", "help", "帮助"}


class SessionManager:
    def __init__(
        self,
        cwd: Path,
        runtime: AgentRuntime | None = None,
        default_limits=None,
        dry_run: bool = False,
        memory_manager: MemoryManager | None = None,
        initial_session: SessionState | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self.runtime = runtime or AgentRuntime(default_limits=default_limits, dry_run=dry_run, memory_manager=memory_manager)
        self.current = initial_session or SessionState.create(cwd=cwd)
        self.current.cwd = cwd
        self.memory_manager = memory_manager or getattr(self.runtime, "memory_manager", None)
        self.session_store = session_store or SessionStore(cwd)

    def handle_user_message(self, content: str, append_user_message: bool = True) -> SessionState:
        normalized = content.strip().lower()
        if normalized in HELP_COMMANDS:
            self.current.messages.append(Message.system(format_help_text()))
            return self._save_current(self.current)
        if normalized in SAVE_CONTEXT_COMMANDS:
            return self._save_current(self.save_context_snapshot())
        if normalized in COMPACT_CONTEXT_COMMANDS:
            return self._save_current(self.compact_context())
        if normalized.startswith("忘记") and self.memory_manager is not None:
            query = content.strip()[2:].strip()
            deleted = self.memory_manager.delete_matching(query) if query else self.memory_manager.delete_all()
            self.current.messages.append(Message.system(f"已删除 {deleted} 条长期记忆。"))
            return self._save_current(self.current)
        if self.current.pending_confirmation is not None:
            if normalized in CONFIRMATION_WORDS:
                return self._save_current(self.accept_pending_confirmation())
            if normalized in DENIAL_WORDS:
                return self._save_current(self.reject_pending_confirmation())
        self.current = self.runtime.on_user_message(content, self.current, append_user_message=append_user_message)
        return self._save_current(self.current)

    def accept_pending_confirmation(self) -> SessionState:
        if self.current.pending_confirmation is None:
            return self.current
        self.current.pending_confirmation.approved = True
        self.current.pending_confirmation.prompt = self.current.pending_confirmation.prompt or "confirmed"
        self.current = self.runtime.on_user_message(
            self.current.active_task.goal if self.current.active_task else "",
            self.current,
            append_user_message=False,
        )
        self.current.pending_confirmation = None
        return self.current

    def reject_pending_confirmation(self) -> SessionState:
        if self.current.pending_confirmation is None:
            return self.current
        self.current.pending_confirmation = None
        self.current.messages.append(Message.system("用户拒绝了待确认写入。"))
        if self.current.active_task is not None:
            self.current.active_task.status = "failed"
            self.current.active_task.result = "用户拒绝了待确认写入。"
        return self.current

    def compact_context(self) -> SessionState:
        if not self.current.messages:
            self.current.messages.append(Message.system("当前没有可压缩的上下文。"))
            return self.current

        original_count = len(self.current.messages)
        estimated_tokens = estimate_message_tokens(self.current.messages)
        target_budget = max(1, int(estimated_tokens * 0.55))
        compacted, snapshot = compact_messages_with_snapshot(self.current.messages, token_budget=target_budget)

        if snapshot is None or len(compacted) >= original_count:
            self.current.messages.append(Message.system("当前上下文无需压缩。"))
            return self.current

        self.current.messages = compacted
        self.current.compression_snapshots.append(snapshot)
        self.current.messages.append(
            Message.system(
                f"已手动压缩上下文：原 {original_count} 条消息，现 {len(compacted)} 条消息。"
            )
        )
        return self.current

    def save_context_snapshot(self) -> SessionState:
        snapshot_dir = self._next_context_snapshot_dir()
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        (snapshot_dir / "session.json").write_text(
            self.current.model_dump_json(indent=2),
            encoding="utf-8",
        )
        (snapshot_dir / "context.md").write_text(
            self._format_context_snapshot_markdown(),
            encoding="utf-8",
        )
        self.current.messages.append(Message.system(f"已保存当前上下文：{snapshot_dir.name}"))
        return self.current

    def _next_context_snapshot_dir(self) -> Path:
        base = datetime.now().strftime("context-%Y%m%d-%H%M%S")
        candidate = self.current.cwd / base
        if not candidate.exists():
            return candidate
        index = 2
        while True:
            candidate = self.current.cwd / f"{base}-{index}"
            if not candidate.exists():
                return candidate
            index += 1

    def _format_context_snapshot_markdown(self) -> str:
        lines = [
            "# Manus Mini Context Snapshot",
            "",
            f"- Session: `{self.current.session_id}`",
            f"- CWD: `{self.current.cwd}`",
            f"- Messages: {len(self.current.messages)}",
        ]
        if self.current.active_task is not None:
            task = self.current.active_task
            lines.extend(
                [
                    f"- Active task: `{task.run_id}`",
                    f"- Task status: `{task.status}`",
                    f"- Task goal: {task.goal}",
                ]
            )
        lines.append("")
        lines.append("## Messages")
        for index, message in enumerate(self.current.messages, start=1):
            lines.extend(
                [
                    "",
                    f"### {index}. {message.role}",
                    "",
                    message.content,
                ]
            )
        if self.current.compression_snapshots:
            lines.append("")
            lines.append("## Compression Snapshots")
            for snapshot in self.current.compression_snapshots:
                lines.extend(["", f"- `{snapshot.id}`: {snapshot.summary}"])
        return "\n".join(lines).rstrip() + "\n"

    def _save_current(self, session: SessionState) -> SessionState:
        self.session_store.save(session)
        return session


def format_help_text() -> str:
    return "\n".join(
        [
            "可用指令",
            "",
            "TUI 内指令",
            "- `/help`：查看当前可用指令与作用。",
            "- `/save-context`：在项目根目录保存当前上下文快照，目录名包含当前时间。",
            "- `/compact` / `压缩上下文`：手动压缩当前对话上下文。",
            "- `忘记 <关键词>`：删除匹配的长期记忆；只输入 `忘记` 会清空长期记忆。",
            "- `确认` / `取消`：处理待确认的写入操作。",
            "- 待确认时会在 TUI 底部显示弹层，可用 ↑/↓ 切换，Enter 确认，Esc 取消。",
            "",
            "CLI 指令",
            "- `manus-mini list --cwd <目录>`：列出指定工作目录下已保存的对话。",
            "- `manus-mini resume <session_id> --cwd <目录>`：恢复指定对话并继续执行。",
        ]
    )
