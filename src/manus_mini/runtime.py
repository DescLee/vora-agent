from __future__ import annotations

from manus_mini.models import LoopLimits, Message, PlanStep, SessionState, TaskState
from manus_mini.reflection import ReflectionLoop


class AgentRuntime:
    def __init__(self, default_limits: LoopLimits | None = None) -> None:
        self.default_limits = default_limits or LoopLimits()
        self.reflection_loop = ReflectionLoop()

    def on_user_message(self, content: str, session: SessionState) -> SessionState:
        session.messages.append(Message.user(content))

        task = TaskState.create(goal=content, cwd=session.cwd, limits=self.default_limits)
        task.plan.append(PlanStep(description=content, intent="research"))

        for _ in range(task.limits.max_engineering_steps):
            task.step_count += 1
            reflection = self.reflection_loop.run(task, session)
            task.result = reflection.content
            task.status = "done"
            break
        else:
            task.result = "已达到外层执行上限，当前没有足够结果。"
            task.status = "failed"

        session.messages.append(Message.agent(task.result))
        session.active_task = task
        return session
