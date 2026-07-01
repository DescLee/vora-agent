from __future__ import annotations

from dataclasses import dataclass

from manus_mini.models import TaskState


@dataclass(slots=True)
class ReflectionDecision:
    decision: str
    reason: str


class Reflector:
    def decide(self, task: TaskState, draft: str) -> ReflectionDecision:
        text = draft.strip()
        last_error = task.errors[-1] if task.errors else None

        if not text:
            return ReflectionDecision("replan", "draft is empty")

        if last_error is not None and last_error.retryable:
            return ReflectionDecision("regenerate", f"retryable error: {last_error.code}")

        if any(keyword in text for keyword in ["待补充", "需要补充", "TODO", "不够", "有风险"]):
            return ReflectionDecision("local_update", "draft needs local refinement")

        if "重新规划" in text or ("重新生成" in text and len(text) < 20):
            return ReflectionDecision("replan", "draft asks for replanning")

        return ReflectionDecision("accept", "draft is sufficient")
