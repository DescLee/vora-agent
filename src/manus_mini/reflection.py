from __future__ import annotations

from pydantic import BaseModel

from manus_mini.models import SessionState, TaskState, TraceEvent
from manus_mini.react import ReActLoop
from manus_mini.reflector import Reflector


class ReflectionResult(BaseModel):
    accepted: bool
    content: str
    reason: str
    decision: str = "accept"


class ReflectionLoop:
    def __init__(self, react_loop: ReActLoop | None = None, reflector: Reflector | None = None) -> None:
        self.react_loop = react_loop or ReActLoop()
        self.reflector = reflector or Reflector()

    def run(self, task: TaskState, session: SessionState) -> ReflectionResult:
        rounds = max(1, task.limits.max_reflection_rounds)
        best_content = ""
        last_decision = "replan"
        last_reason = "no reflection output"

        for _ in range(rounds):
            draft = self.react_loop.run(task, session)
            best_content = draft
            reflection = self.reflector.decide(task, draft)
            last_decision = reflection.decision
            last_reason = reflection.reason
            task.trace_events.append(
                TraceEvent(
                    phase="reflection",
                    message=f"Reflection decided {reflection.decision}",
                    data={
                        "decision": reflection.decision,
                        "reason": reflection.reason,
                        "draft_preview": draft[:500],
                    },
                )
            )
            if reflection.decision == "accept":
                return ReflectionResult(accepted=True, content=draft, reason=reflection.reason, decision=reflection.decision)
            if reflection.decision == "replan":
                break

        return ReflectionResult(
            accepted=last_decision == "accept",
            content=best_content or "已达到反思上限，保留当前最佳结果。",
            reason=last_reason or "max reflection rounds reached",
            decision=last_decision,
        )
