from __future__ import annotations

from datetime import datetime
from pathlib import Path
from time import monotonic

from manus_mini.logging import EventLogger
from manus_mini.models import AgentError, Artifact, LoopLimits, Message, PlanStep, SessionState, TaskState, TraceEvent
from manus_mini.reflection import ReflectionLoop
from manus_mini.reporter import Reporter


def format_runtime_exception(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    return f"执行失败：{message}"


def format_runtime_timeout(limit_seconds: int) -> str:
    return f"运行超时：本轮执行超过 {limit_seconds} 秒限制，已停止并保留当前过程。"


class AgentRuntime:
    def __init__(
        self,
        default_limits: LoopLimits | None = None,
        logger: EventLogger | None = None,
        reporter: Reporter | None = None,
    ) -> None:
        self.default_limits = default_limits or LoopLimits()
        self.reflection_loop = ReflectionLoop()
        self.logger = logger or EventLogger(Path("runs"))
        self.reporter = reporter or Reporter(Path("outputs"))

    def on_user_message(
        self,
        content: str,
        session: SessionState,
        append_user_message: bool = True,
    ) -> SessionState:
        if append_user_message:
            session.messages.append(Message.user(content))

        task = TaskState.create(goal=content, cwd=session.cwd, limits=self.default_limits)
        task.plan.append(PlanStep(description=content, intent="research"))
        session.active_task = task
        started_at = monotonic()

        for _ in range(task.limits.max_engineering_steps):
            if self._runtime_exceeded(started_at, task.limits.max_runtime_seconds):
                self._mark_runtime_timeout(task)
                break
            task.step_count += 1
            self.logger.record(
                task.run_id,
                {
                    "type": "engineering_step",
                    "step_count": task.step_count,
                    "goal": task.goal,
                },
            )
            try:
                reflection = self.reflection_loop.run(task, session)
            except Exception as error:
                error_code = "MAX_REACT_ITERATIONS_REACHED" if str(error) == "MAX_REACT_ITERATIONS_REACHED" else "LLM_ERROR"
                task.errors.append(
                    AgentError(
                        code=error_code,
                        message=str(error) or error.__class__.__name__,
                        retryable=True,
                    )
                )
                task.trace_events.append(
                    TraceEvent(
                        phase="runtime",
                        message="Runtime caught execution error",
                        data={"code": error_code, "message": task.errors[-1].message},
                    )
                )
                task.result = format_runtime_exception(error)
                task.status = "failed"
                self.logger.record(
                    task.run_id,
                    {
                        "type": "error",
                        "code": task.errors[-1].code,
                        "message": task.errors[-1].message,
                    },
                )
            else:
                if self._runtime_exceeded(started_at, task.limits.max_runtime_seconds):
                    self._mark_runtime_timeout(task)
                else:
                    task.result = reflection.content
                    task.status = "done"
            break
        else:
            task.result = "已达到外层执行上限，当前没有足够结果。"
            task.status = "failed"

        artifact_path = self.reporter.write_task_report(
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{task.run_id}.md",
            task,
            content,
        )
        artifact = Artifact(path=artifact_path, kind="markdown", summary="runtime result")
        task.artifacts.append(artifact)
        self.logger.record(
            task.run_id,
            {
                "type": "result",
                "status": task.status,
                "artifact": artifact.path.as_posix(),
            },
        )
        session.messages.append(Message.agent(task.result))
        session.active_task = task
        return session

    def _runtime_exceeded(self, started_at: float, max_runtime_seconds: int) -> bool:
        return monotonic() - started_at > max_runtime_seconds

    def _mark_runtime_timeout(self, task: TaskState) -> None:
        task.errors.append(
            AgentError(
                code="RUNTIME_TIMEOUT",
                message=f"runtime exceeded {task.limits.max_runtime_seconds} seconds",
                retryable=True,
            )
        )
        task.trace_events.append(
            TraceEvent(
                phase="runtime",
                message="Runtime timeout reached",
                data={"max_runtime_seconds": task.limits.max_runtime_seconds},
            )
        )
        task.result = format_runtime_timeout(task.limits.max_runtime_seconds)
        task.status = "failed"
