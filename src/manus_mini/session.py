from __future__ import annotations

from pathlib import Path

from manus_mini.context import compact_messages_with_snapshot, estimate_message_tokens
from manus_mini.models import Message, SessionState
from manus_mini.memory import MemoryManager
from manus_mini.runtime import AgentRuntime

CONFIRMATION_WORDS = {"y", "yes", "confirm", "confirmed", "确认", "同意", "好", "继续"}
DENIAL_WORDS = {"n", "no", "cancel", "cancelled", "取消", "否", "不要"}
COMPACT_CONTEXT_COMMANDS = {"压缩上下文", "手动压缩上下文", "/compact", "compact context"}


class SessionManager:
    def __init__(
        self,
        cwd: Path,
        runtime: AgentRuntime | None = None,
        default_limits=None,
        dry_run: bool = False,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        self.runtime = runtime or AgentRuntime(default_limits=default_limits, dry_run=dry_run, memory_manager=memory_manager)
        self.current = SessionState.create(cwd=cwd)
        self.memory_manager = memory_manager or getattr(self.runtime, "memory_manager", None)

    def handle_user_message(self, content: str, append_user_message: bool = True) -> SessionState:
        normalized = content.strip().lower()
        if normalized in COMPACT_CONTEXT_COMMANDS:
            return self.compact_context()
        if normalized.startswith("忘记") and self.memory_manager is not None:
            query = content.strip()[2:].strip()
            deleted = self.memory_manager.delete_matching(query) if query else self.memory_manager.delete_all()
            self.current.messages.append(Message.system(f"已删除 {deleted} 条长期记忆。"))
            return self.current
        if self.current.pending_confirmation is not None:
            if normalized in CONFIRMATION_WORDS:
                self.current.pending_confirmation.approved = True
                self.current.pending_confirmation.prompt = self.current.pending_confirmation.prompt or content.strip()
                self.current = self.runtime.on_user_message(
                    self.current.active_task.goal if self.current.active_task else content,
                    self.current,
                    append_user_message=False,
                )
                self.current.pending_confirmation = None
                return self.current
            if normalized in DENIAL_WORDS:
                self.current.pending_confirmation = None
                self.current.messages.append(Message.system("用户拒绝了待确认写入。"))
                if self.current.active_task is not None:
                    self.current.active_task.status = "failed"
                    self.current.active_task.result = "用户拒绝了待确认写入。"
                return self.current
        self.current = self.runtime.on_user_message(content, self.current, append_user_message=append_user_message)
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
