from __future__ import annotations

from pydantic import BaseModel

from manus_mini.logging import EventLogger
from manus_mini.models import Message, SessionState, TaskState, TraceEvent
from manus_mini.react import ReActLoop
from manus_mini.reflector import Reflector


class ReflectionResult(BaseModel):
    accepted: bool
    content: str
    reason: str
    decision: str = "accept"


class ReflectionLoop:
    def __init__(
        self,
        react_loop: ReActLoop | None = None,
        reflector: Reflector | None = None,
        logger: EventLogger | None = None,
    ) -> None:
        self.react_loop = react_loop or ReActLoop()
        self.reflector = reflector or Reflector()
        self.logger = logger

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
                    message=f"Reflection decided {reflection.decision}: {reflection.reason}",
                    data={
                        "decision": reflection.decision,
                        "reason": reflection.reason,
                        "draft_preview": draft[:500],
                    },
                )
            )
            self._record_reflection_result(task, draft, reflection.decision, reflection.reason)
            if reflection.decision == "accept":
                return ReflectionResult(accepted=True, content=draft, reason=reflection.reason, decision=reflection.decision)
            session.messages.append(
                Message.system(self._build_follow_up_context(task, draft, reflection.reason))
            )
            if reflection.decision == "replan":
                break

        return ReflectionResult(
            accepted=last_decision == "accept",
            content=best_content or "已达到反思上限，保留当前最佳结果。",
            reason=last_reason or "max reflection rounds reached",
            decision=last_decision,
        )

    def _build_follow_up_context(self, task: TaskState, draft: str, reason: str) -> str:
        recent_observations = [
            observation.summary
            for observation in task.observations[-5:]
            if observation.summary.strip()
        ]
        lines = [
            "上一轮已完成的进展，请在此基础上继续，不要从头重扫：",
            f"- 反思原因：{reason or '需要继续修订'}",
            f"- 上一轮草稿：{_short_text(draft, limit=240)}",
        ]
        if recent_observations:
            lines.append("- 最近工具结果：")
            lines.extend(f"  - {summary}" for summary in recent_observations)
        return "\n".join(lines)

    def _record_reflection_result(self, task: TaskState, draft: str, decision: str, reason: str) -> None:
        if self.logger is None:
            return
        self.logger.record(
            task.session_id or "unknown-session",
            task.run_id,
            {
                "type": "reflection",
                "decision": decision,
                "reason": reason,
                "accepted": decision == "accept",
                "draft_preview": _short_text(draft, limit=500),
            },
        )


def _short_text(content: str, limit: int = 160) -> str:
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"
