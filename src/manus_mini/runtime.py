from __future__ import annotations

from inspect import signature
from datetime import datetime
from pathlib import Path

from manus_mini.logging import EventLogger, project_outputs_dir, project_runs_dir
from manus_mini.context import (
    build_context_bundle,
    estimate_session_context_usage,
)
from manus_mini.models import AgentError, Artifact, LoopLimits, Message, PlanStep, SessionState, TaskState, TraceEvent
from manus_mini.memory import MemoryManager
from manus_mini.planner import Planner
from manus_mini.reflection import ReflectionLoop
from manus_mini.reporter import Reporter
from manus_mini.react import ReActLoop
from manus_mini.llm import LLMClient


def format_runtime_exception(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    return f"执行失败：{message}"


class AgentRuntime:
    def __init__(
        self,
        default_limits: LoopLimits | None = None,
        logger: EventLogger | None = None,
        reporter: Reporter | None = None,
        dry_run: bool = False,
        memory_manager: MemoryManager | None = None,
        llm: LLMClient | None = None,
        cwd: Path | None = None,
    ) -> None:
        self.cwd = cwd or Path.cwd()
        self.default_limits = default_limits or LoopLimits()
        self.logger = logger or EventLogger(project_runs_dir(self.cwd))
        self.react_loop = ReActLoop(llm=llm, dry_run=dry_run, logger=self.logger)
        self.reflection_loop = ReflectionLoop(react_loop=self.react_loop, llm=llm, logger=self.logger)
        self.planner = Planner(llm=llm, logger=self.logger)
        self.reporter = reporter or Reporter(self._default_reporter_output_dir(), run_root=self._default_reporter_run_root())
        self.dry_run = dry_run
        self.memory_manager = memory_manager or MemoryManager(":memory:")

    def _default_reporter_output_dir(self) -> Path:
        return project_outputs_dir(self.cwd)

    def _default_reporter_run_root(self) -> Path:
        return project_runs_dir(self.cwd)

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
                    "已有产物:\n"
                    f"{session.active_task.result}"
                )
            )
        relevant_memories = self._inject_relevant_memories(session, content)

        task = TaskState.create(goal=content, cwd=session.cwd, limits=self.default_limits)
        task.session_id = session.session_id
        context_limit_getter = getattr(self.react_loop.llm, "context_limit", None)
        model_context_limit = context_limit_getter() if callable(context_limit_getter) else None
        task.model_context_limit = model_context_limit or task.limits.max_estimated_tokens
        self._build_context_bundle(session, content, relevant_memories)
        plan_steps, plan_reasoning = self._build_plan(content, session, task.run_id)
        task.plan = plan_steps
        task.plan_reasoning_content = plan_reasoning
        task.trace_events.append(
            TraceEvent(
                phase="runtime",
                message="规划器已生成初始计划",
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
        interrupted = False
        try:
            last_result = ""
            estimated_tokens, context_usage = estimate_session_context_usage(session, task.model_context_limit)
            self.logger.record(
                session.session_id,
                task.run_id,
                {
                    "type": "context_budget",
                    "estimated_tokens": estimated_tokens,
                    "model_context_limit": task.model_context_limit,
                    "context_usage": context_usage,
                    "compression_triggered": context_usage is not None and context_usage >= 0.70,
                    "message_count": len(session.messages),
                    "memory_refs": list(session.memory_refs),
                },
            )

            for _ in range(task.limits.max_engineering_steps):
                task.step_count += 1
                self._mark_plan_running(task)
                task.status = "reflecting"
                self.logger.record(
                    session.session_id,
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
                        session.session_id,
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
                    if reflection.accepted:
                        self._mark_plan_done(task)
                        task.status = "done"
                        break
                    if _reflection_waits_for_user_choice(reflection.reason, reflection.content):
                        task.trace_events.append(
                            TraceEvent(
                                phase="runtime",
                                message="Runtime stopped: waiting for user choice",
                                data={
                                    "decision": reflection.decision,
                                    "reason": reflection.reason,
                                },
                            )
                        )
                        task.status = "done"
                        break
                    if reflection.decision == "replan":
                        plan_steps, plan_reasoning = self._build_plan(f"{content}\n{reflection.reason}", session, task.run_id)
                        task.plan = plan_steps
                        task.plan_reasoning_content = plan_reasoning
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
        except KeyboardInterrupt:
            interrupted = True
            self._mark_user_cancelled(task)

        artifact_path = self.reporter.write_task_report(
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{task.run_id}.md",
            task,
            content,
        )
        artifact = Artifact(path=artifact_path, kind="markdown", summary="runtime result")
        task.artifacts.append(artifact)
        self._persist_memory_from_turn(session, task, content)
        self.logger.record(
            session.session_id,
            task.run_id,
            {
                "type": "result",
                "status": task.status,
                "artifact": artifact.path.as_posix(),
                "interrupted": interrupted,
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

    def _build_plan(self, content: str, session: SessionState, run_id: str) -> tuple[list[PlanStep], str]:
        build_plan = self.planner.build_plan
        if "run_id" in signature(build_plan).parameters:
            return _normalize_plan_result(build_plan(content, session, run_id=run_id))
        return _normalize_plan_result(build_plan(content, session))

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
            session.session_id,
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

    def _mark_user_cancelled(self, task: TaskState) -> None:
        task.errors.append(
            AgentError(
                code="USER_CANCELLED",
                message="execution interrupted by user",
                retryable=False,
            )
        )
        task.trace_events.append(
            TraceEvent(
                phase="runtime",
                message="Execution interrupted by user",
                data={"code": "USER_CANCELLED"},
            )
        )
        task.result = "执行已被用户中断，已保留当前进度。"
        task.status = "failed"
        self.logger.record(
            task.session_id or "unknown-session",
            task.run_id,
            {
                "type": "interrupt",
                "code": "USER_CANCELLED",
                "message": task.result,
                "step_count": task.step_count,
            },
        )


def _reflection_waits_for_user_choice(reason: str, content: str) -> bool:
    text = f"{reason}\n{content}".lower()
    return any(
        phrase in text
        for phrase in [
            "等待用户选择",
            "等待用户确认",
            "用户选择后",
            "用户确认后",
            "请选择",
            "请选",
            "选一个",
            "选好后",
            "等你选",
            "等你确认",
            "waiting for user",
            "wait for user",
        ]
    )


def _normalize_plan_result(result) -> tuple[list[PlanStep], str]:
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], str):
        return list(result[0]), result[1]
    return list(result), ""
