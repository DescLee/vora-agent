from __future__ import annotations

from pydantic import BaseModel

from manus_mini.models import SessionState, TaskState
from manus_mini.react import ReActLoop


class ReflectionResult(BaseModel):
    accepted: bool
    content: str
    reason: str


class ReflectionLoop:
    def __init__(self, react_loop: ReActLoop | None = None) -> None:
        self.react_loop = react_loop or ReActLoop()

    def run(self, task: TaskState, session: SessionState) -> ReflectionResult:
        rounds = max(1, task.limits.max_reflection_rounds)
        best_content = ""

        for _ in range(rounds):
            draft = self.react_loop.run(task, session)
            best_content = draft
            if draft.strip():
                return ReflectionResult(accepted=True, content=draft, reason="accepted")

        return ReflectionResult(
            accepted=True,
            content=best_content or "已达到反思上限，保留当前最佳结果。",
            reason="max reflection rounds reached",
        )
