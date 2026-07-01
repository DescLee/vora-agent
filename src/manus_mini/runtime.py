from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from time import monotonic

from manus_mini.logging import EventLogger
from manus_mini.context import build_context_bundle, estimate_session_context_usage
from manus_mini.models import AgentError, Artifact, LoopLimits, Message, SessionState, TaskState, TraceEvent
from manus_mini.memory import MemoryManager
from manus_mini.planner import Planner
from manus_mini.reflection import ReflectionLoop
from manus_mini.reporter import Reporter
from manus_mini.react import ReActLoop


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
        dry_run: bool = False,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        self.default_limits = default_limits or LoopLimits()
        self.logger = logger or EventLogger(Path("runs"))
        self.react_loop = ReActLoop(dry_run=dry_run, logger=self.logger)
        self.reflection_loop = ReflectionLoop(react_loop=self.react_loop)
        self.planner = Planner()
        self.reporter = reporter or Reporter(self._default_reporter_output_dir())
        self.dry_run = dry_run
        self.memory_manager = memory_manager or MemoryManager(":memory:")

    def _default_reporter_output_dir(self) -> Path:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return Path(tempfile.gettempdir()) / "manus-mini" / "outputs"
        return Path("outputs")

    def on_user_message(
        self,
        content: str,
        session: SessionState,
        append_user_message: bool = True,
    ) -> SessionState:
        if append_user_message:
            session.messages.append(Message.user(content))
        if session.active_task is not None and session.active_task.result:
            session.messages.append(
                Message.system(
                    "当前产物:\n"
                    f"{session.active_task.result}"
                )
            )
        relevant_memories = self._inject_relevant_memories(session, content)

        task = TaskState.create(goal=content, cwd=session.cwd, limits=self.default_limits)
        self._build_context_bundle(session, content, relevant_memories)
        task.plan = self.planner.build_plan(content, session)
        task.trace_events.append(
            TraceEvent(
                phase="runtime",
                message="Planner generated initial plan",
                data={
                    "steps": [
                        {
                            "description": step.description,
                            "intent": step.intent,
                            "status": step.status,
                        }
                        for step in task.plan
                    ]
                },
            )
        )
        session.active_task = task
        if self._is_chat_only_task(task):
            return self._complete_chat_task(content, session, task)
        started_at = monotonic()
        last_result = ""
        estimated_tokens, context_usage = estimate_session_context_usage(session, task.limits.max_estimated_tokens)
        self.logger.record(
            task.run_id,
            {
                "type": "context_budget",
                "estimated_tokens": estimated_tokens,
                "model_context_limit": task.limits.max_estimated_tokens,
                "context_usage": context_usage,
                "compression_triggered": context_usage is not None and context_usage >= 0.70,
                "message_count": len(session.messages),
                "memory_refs": list(session.memory_refs),
            },
        )

        for _ in range(task.limits.max_engineering_steps):
            if self._runtime_exceeded(started_at, task.limits.max_runtime_seconds):
                self._mark_runtime_timeout(task)
                break
            task.step_count += 1
            self._mark_plan_running(task)
            task.status = "reflecting"
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
                error_code = "MAX_REACT_ITERATIONS_REACHED"
                if str(error) == "TOKEN_BUDGET_EXCEEDED":
                    error_code = "TOKEN_BUDGET_EXCEEDED"
                elif str(error) != "MAX_REACT_ITERATIONS_REACHED":
                    error_code = "LLM_ERROR"
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
                break
            else:
                last_result = reflection.content
                task.result = reflection.content
                task.trace_events.append(
                    TraceEvent(
                        phase="runtime",
                        message="Reflection result applied",
                        data={
                            "decision": reflection.decision,
                            "accepted": reflection.accepted,
                            "reason": reflection.reason,
                        },
                    )
                )
                if session.pending_confirmation is not None and not session.pending_confirmation.approved:
                    task.status = "waiting_confirmation"
                    task.result = session.pending_confirmation.prompt or session.pending_confirmation.summary or "需要用户确认写入"
                    break
                if self._runtime_exceeded(started_at, task.limits.max_runtime_seconds):
                    self._mark_runtime_timeout(task)
                    break
                if reflection.accepted:
                    self._mark_plan_done(task)
                    task.status = "done"
                    break
                if reflection.decision == "replan":
                    task.plan = self.planner.build_plan(f"{content}\n{reflection.reason}", session)
                    self._mark_plan_running(task)
                    task.trace_events.append(
                        TraceEvent(
                            phase="runtime",
                            message="Planner regenerated plan after reflection",
                            data={
                                "reason": reflection.reason,
                                "steps": [
                                    {
                                        "description": step.description,
                                        "intent": step.intent,
                                        "status": step.status,
                                    }
                                    for step in task.plan
                                ],
                            },
                        )
                    )
                elif reflection.decision == "regenerate":
                    session.messages.append(
                        Message.system(f"请基于现有草稿重新生成，并补强细节：{reflection.reason}")
                    )
                elif reflection.decision == "local_update":
                    session.messages.append(
                        Message.system(f"请对当前草稿做局部修订：{reflection.reason}")
                    )
                task.status = "planning"
        else:
            task.result = last_result or "已达到外层执行上限，保留当前最佳结果。下一步建议继续缩小目标或让 Agent 继续反思。"
            task.status = "failed"

        artifact_path = self.reporter.write_task_report(
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{task.run_id}.md",
            task,
            content,
        )
        artifact = Artifact(path=artifact_path, kind="markdown", summary="runtime result")
        task.artifacts.append(artifact)
        self._persist_memory_from_turn(session, task, content)
        self.logger.record(
            task.run_id,
            {
                "type": "result",
                "status": task.status,
                "artifact": artifact.path.as_posix(),
            },
        )
        session.messages.append(Message.agent(task.result))
        if task.status in {"done", "failed"}:
            session.pending_confirmation = None
        session.active_task = task
        return session

    def _mark_plan_running(self, task: TaskState) -> None:
        if not task.plan:
            return
        index = min(max(task.current_step_index, 0), len(task.plan) - 1)
        for position, step in enumerate(task.plan):
            if step.status == "done":
                continue
            if position < index:
                step.status = "done"
            elif position == index:
                step.status = "running"
            else:
                step.status = "pending"

    def _mark_plan_done(self, task: TaskState) -> None:
        for step in task.plan:
            if step.status != "skipped":
                step.status = "done"

    def _is_chat_only_task(self, task: TaskState) -> bool:
        return bool(task.plan) and all(step.intent == "chat" for step in task.plan)

    def _complete_chat_task(self, content: str, session: SessionState, task: TaskState) -> SessionState:
        for step in task.plan:
            step.status = "done"
        task.step_count = 1
        task.status = "done"
        task.result = self._chat_reply(content)
        task.trace_events.append(
            TraceEvent(
                phase="llm",
                message="Planner routed message to direct chat",
                data={"content_preview": task.result, "tool_calls": []},
            )
        )
        self.logger.record(
            task.run_id,
            {
                "type": "result",
                "status": task.status,
                "chat_only": True,
            },
        )
        session.messages.append(Message.agent(task.result))
        session.active_task = task
        return session

    def _chat_reply(self, content: str) -> str:
        if any(keyword in content for keyword in ["你好", "您好", "hello", "hi"]):
            return "你好，我在。你可以直接继续说需求；如果需要我查看当前项目或文件，再明确告诉我。"
        return f"我先按普通对话理解，不读取本地文件。你刚才说的是：{content}"

    def _inject_relevant_memories(self, session: SessionState, content: str) -> None:
        if self.memory_manager is None:
            return []
        memories = self.memory_manager.search(content, limit=3)
        if not memories:
            fallback_queries = []
            for keyword in ["偏好", "项目", "Markdown", "报告"]:
                if keyword in content:
                    fallback_queries.append(keyword)
            for query in fallback_queries:
                memories = self.memory_manager.search(query, limit=3)
                if memories:
                    break
        if not memories:
            return []
        session.memory_refs = [memory.id for memory in memories]
        memory_lines = [f"- {memory.content}" for memory in memories]
        session.messages.append(Message.system("长期记忆:\n" + "\n".join(memory_lines)))
        return memories

    def _persist_memory_from_turn(self, session: SessionState, task: TaskState, content: str) -> None:
        if self.memory_manager is None:
            return
        normalized = content.lower()
        if any(keyword in normalized for keyword in ["偏好", "喜欢", "以后", "尽量", "总是"]):
            memory = self.memory_manager.add_if_allowed(
                scope="user",
                kind="preference",
                content=content,
                tags=["preference", "user"],
                source_message_ids=[message.id for message in session.messages if message.role == "user"][-3:],
            )
            if memory is not None:
                session.memory_refs.append(memory.id)
        if any(keyword in content for keyword in ["项目", "结构", "技术栈"]):
            memory = self.memory_manager.add_if_allowed(
                scope="project",
                kind="project_summary",
                content=task.result or content,
                tags=["project", "summary"],
                source_message_ids=[message.id for message in session.messages if message.role == "user"][-3:],
            )
            if memory is not None:
                session.memory_refs.append(memory.id)

    def _build_context_bundle(self, session: SessionState, content: str, relevant_memories) -> None:
        if not session.messages:
            return
        current_user_message = Message.user(content)
        bundle = build_context_bundle(
            current_user_message=current_user_message,
            recent_messages=session.messages[-20:],
            relevant_memories=relevant_memories,
            compression_summaries=session.compression_snapshots[-5:],
            active_artifacts=session.artifacts[-5:],
            recent_observations=session.active_task.observations[-10:] if session.active_task is not None else [],
        )
        self.logger.record(
            session.active_task.run_id if session.active_task is not None else "unknown",
            {
                "type": "context_bundle",
                "recent_messages": len(bundle.recent_messages),
                "relevant_memories": len(bundle.relevant_memories),
                "compression_summaries": len(bundle.compression_summaries),
                "active_artifacts": len(bundle.active_artifacts),
                "recent_observations": len(bundle.recent_observations),
            },
        )

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
