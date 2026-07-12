from __future__ import annotations

from datetime import datetime
from inspect import signature
from pathlib import Path

from vora.config import AppConfig
from vora.context import (
    build_context_bundle,
    complete_interrupted_tool_messages,
    estimate_session_context_usage,
)
from vora.llm import LLMClient, get_default_llm_client
from vora.logging import EventLogger, project_logs_dir, project_outputs_dir
from vora.memory import MemoryManager
from vora.models import (
    AgentError,
    AgentErrorCode,
    Artifact,
    LoopLimits,
    MemoryItem,
    Message,
    PlanStep,
    SessionState,
    TaskState,
    TraceEvent,
)
from vora.planner import Planner
from vora.reflection import ReflectionLoop
from vora.reporter import Reporter
from vora.react import ReActLoop
from vora.skills import SkillRegistry, SkillSpec
from vora.task_strategy import select_task_strategy


def format_runtime_exception(error: Exception) -> str:
    message = str(error).strip() or error.__class__.__name__
    return f"执行失败：{message}"


CODE_REVIEW_MAX_REACT_ITERATIONS = 999


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
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self.cwd = cwd or Path.cwd()
        self.default_limits = default_limits or LoopLimits()
        self.logger = logger or EventLogger(project_logs_dir(self.cwd))
        self.react_loop = ReActLoop(llm=llm, dry_run=dry_run, logger=self.logger)
        self.reflection_loop = ReflectionLoop(react_loop=self.react_loop, llm=llm, logger=self.logger)
        self.planner = Planner(llm=llm, logger=self.logger)
        self.reporter = reporter or Reporter(self._default_reporter_output_dir(), run_root=self._default_reporter_run_root())
        self.dry_run = dry_run
        self.memory_manager = memory_manager or MemoryManager(":memory:")
        self.skill_registry = skill_registry or SkillRegistry.default(self.cwd)

    def resolve_model_context_limit(self) -> int | None:
        llm = getattr(self.react_loop, "llm", None)
        if llm is None:
            try:
                llm = get_default_llm_client(AppConfig.from_env(self.cwd / ".env"))
            except Exception:
                return None
            self.react_loop.llm = llm
            if hasattr(self.reflection_loop, "llm") and self.reflection_loop.llm is None:
                self.reflection_loop.llm = llm
            if hasattr(self.planner, "llm") and self.planner.llm is None:
                self.planner.llm = llm
        context_limit_getter = getattr(llm, "context_limit", None)
        if not callable(context_limit_getter):
            return None
        try:
            return context_limit_getter()
        except Exception:
            return None

    def _default_reporter_output_dir(self) -> Path:
        return project_outputs_dir(self.cwd)

    def _default_reporter_run_root(self) -> Path:
        return project_logs_dir(self.cwd)

    def on_user_message(
        self,
        content: str,
        session: SessionState,
        append_user_message: bool = True,
    ) -> SessionState:
        user_message_id = None
        if append_user_message:
            user_message = Message.user(content)
            session.messages.append(user_message)
            user_message_id = user_message.id
        if session.active_task is not None and session.active_task.status == "done" and session.active_task.result:
            session.messages.append(
                Message.system(
                    "已有产物:\n"
                    f"{session.active_task.result}"
                )
            )
        relevant_memories = self._inject_relevant_memories(session, content)

        task = TaskState.create(goal=content, cwd=session.cwd, limits=self.default_limits.model_copy(deep=True))
        task.session_id = session.session_id
        active_skill = self.skill_registry.match(content)
        if active_skill is not None:
            task.metadata["active_skill"] = active_skill.model_dump()
            self._record_active_skill(session, task, active_skill)
        task.metadata["compression_snapshot_start_index"] = len(session.compression_snapshots)
        self.logger.record(
            session.session_id,
            task.run_id,
            {
                "type": "user_input",
                "content": content,
                "message_id": user_message_id,
            },
        )
        if session.model_context_limit is None or session.model_context_limit <= 0:
            model_context_limit = self.resolve_model_context_limit()
            session.model_context_limit = model_context_limit or task.limits.max_estimated_tokens
        task.model_context_limit = session.model_context_limit
        pre_compression_tokens, pre_compression_usage = estimate_session_context_usage(session, task.model_context_limit)
        task.metadata["pre_compression_context_tokens"] = pre_compression_tokens
        task.metadata["pre_compression_context_usage"] = pre_compression_usage
        self.react_loop._compress_session_context_if_needed(task, session, trigger_stage="after_user_message")
        self._build_context_bundle(session, content, relevant_memories)
        plan_steps, plan_reasoning = self._build_plan(content, session, task.run_id, active_skill=active_skill)
        task.plan = plan_steps
        task.plan_reasoning_content = plan_reasoning
        _apply_task_strategy(task)
        _apply_read_efficiency_limits(task)
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
            logged_tokens = task.metadata.get("pre_compression_context_tokens", estimated_tokens)
            logged_usage = task.metadata.get("pre_compression_context_usage", context_usage)
            self.logger.record(
                session.session_id,
                task.run_id,
                {
                    "type": "context_budget",
                    "estimated_tokens": logged_tokens,
                    "model_context_limit": task.model_context_limit,
                    "context_usage": logged_usage,
                    "post_compression_estimated_tokens": estimated_tokens,
                    "post_compression_context_usage": context_usage,
                    "compression_triggered": logged_usage is not None and logged_usage > 0.50,
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
                    error_code: AgentErrorCode = "MAX_REACT_ITERATIONS_REACHED"
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
                    task.result = self._ensure_non_empty_result(task, session, reflection.content)
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
                        plan_steps, plan_reasoning = self._build_plan(
                            f"{content}\n{reflection.reason}",
                            session,
                            task.run_id,
                            active_skill=_active_skill_from_task(task),
                        )
                        task.plan = plan_steps
                        task.plan_reasoning_content = plan_reasoning
                        _apply_task_strategy(task)
                        _apply_read_efficiency_limits(task)
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
                task.result = self._ensure_non_empty_result(
                    task,
                    session,
                    last_result or "已达到外层执行上限，保留当前最佳结果。下一步建议继续缩小目标或让 Agent 继续反思。",
                )
                task.status = "failed"
        except KeyboardInterrupt:
            interrupted = True
            complete_interrupted_tool_messages(session.messages)
            self._mark_user_cancelled(task)

        artifact_path = self.reporter.write_task_report(
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{task.run_id}.md",
            task,
            content,
        )
        task.result = self._ensure_non_empty_result(task, session, task.result)
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
        self.logger.record_summary(
            session.session_id,
            task.run_id,
            user_input=content,
            result=task.result,
            status=task.status,
        )
        session.messages.append(Message.agent(task.result))
        if task.status in {"done", "failed"}:
            session.pending_confirmation = None
        session.active_task = task
        return session

    def continue_active_task_after_confirmation(self, session: SessionState) -> SessionState:
        task = session.active_task
        if task is None:
            return session

        content = task.goal
        interrupted = False
        try:
            last_result = ""
            remaining_steps = max(1, task.limits.max_engineering_steps - task.step_count)
            for _ in range(remaining_steps):
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
                        "resumed_after_confirmation": True,
                    },
                )
                try:
                    reflection = self.reflection_loop.run(task, session)
                except Exception as error:
                    error_code: AgentErrorCode = "MAX_REACT_ITERATIONS_REACHED"
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
                            message="Runtime caught execution error after confirmation",
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
                    task.result = self._ensure_non_empty_result(task, session, reflection.content)
                    task.trace_events.append(
                        TraceEvent(
                            phase="runtime",
                            message="Reflection result applied after confirmation",
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
                                message="Runtime stopped after confirmation: waiting for user choice",
                                data={
                                    "decision": reflection.decision,
                                    "reason": reflection.reason,
                                },
                            )
                        )
                        task.status = "done"
                        break
                    if reflection.decision == "replan":
                        plan_steps, plan_reasoning = self._build_plan(
                            f"{content}\n{reflection.reason}",
                            session,
                            task.run_id,
                            active_skill=_active_skill_from_task(task),
                        )
                        task.plan = plan_steps
                        task.plan_reasoning_content = plan_reasoning
                        _apply_task_strategy(task)
                        _apply_read_efficiency_limits(task)
                        self._mark_plan_running(task)
                        task.trace_events.append(
                            TraceEvent(
                                phase="runtime",
                                message="Planner regenerated plan after confirmation reflection",
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
                task.result = self._ensure_non_empty_result(
                    task,
                    session,
                    last_result or "已达到外层执行上限，保留当前最佳结果。下一步建议继续缩小目标或让 Agent 继续反思。",
                )
                task.status = "failed"
        except KeyboardInterrupt:
            interrupted = True
            complete_interrupted_tool_messages(session.messages)
            self._mark_user_cancelled(task)

        artifact_path = self.reporter.write_task_report(
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{task.run_id}.md",
            task,
            content,
        )
        task.result = self._ensure_non_empty_result(task, session, task.result)
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
                "resumed_after_confirmation": True,
            },
        )
        self.logger.record_summary(
            session.session_id,
            task.run_id,
            user_input=content,
            result=task.result,
            status=task.status,
        )
        session.messages.append(Message.agent(task.result))
        if task.status in {"done", "failed"}:
            session.pending_confirmation = None
        session.active_task = task
        return session

    def _ensure_non_empty_result(self, task: TaskState, session: SessionState, result: str) -> str:
        if result.strip():
            return result
        fallback = self.react_loop._rule_fallback_content(
            task,
            session.messages,
            reason="LLM returned empty content",
        )
        return fallback or "执行完成，但模型没有返回可展示内容。"

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

    def _record_active_skill(self, session: SessionState, task: TaskState, skill: SkillSpec) -> None:
        data = {
            "message_type": "skill_activated",
            "skill_name": skill.name,
            "description": skill.description,
            "tool_allowlist": list(skill.tool_allowlist),
            "acceptance": list(skill.acceptance),
        }
        task.trace_events.append(
            TraceEvent(
                phase="runtime",
                message=f"启用 Skill：{skill.name}",
                data=data,
            )
        )
        self.logger.record(
            session.session_id,
            task.run_id,
            {
                "type": "active_skill",
                "skill_name": skill.name,
                "description": skill.description,
                "tool_allowlist": list(skill.tool_allowlist),
                "acceptance": list(skill.acceptance),
            },
        )

    def _build_plan(
        self,
        content: str,
        session: SessionState,
        run_id: str,
        active_skill: SkillSpec | None = None,
    ) -> tuple[list[PlanStep], str]:
        build_plan = self.planner.build_plan
        parameters = signature(build_plan).parameters
        supports_run_id = "run_id" in parameters
        supports_active_skill = "active_skill" in parameters
        if supports_run_id and supports_active_skill:
            return _normalize_plan_result(
                build_plan(content, session, run_id=run_id, active_skill=active_skill),
                self.planner,
            )
        if supports_run_id:
            return _normalize_plan_result(build_plan(content, session, run_id=run_id), self.planner)
        if supports_active_skill:
            return _normalize_plan_result(build_plan(content, session, active_skill=active_skill), self.planner)
        return _normalize_plan_result(build_plan(content, session), self.planner)

    def _inject_relevant_memories(self, session: SessionState, content: str) -> list[MemoryItem]:
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
            recent_messages=session.messages[-40:],
            relevant_memories=relevant_memories,
            compression_summaries=session.compression_snapshots[-5:],
            active_artifacts=session.artifacts[-5:],
            recent_observations=session.active_task.observations[-16:] if session.active_task is not None else [],
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


def _active_skill_from_task(task: TaskState) -> SkillSpec | None:
    raw_skill = task.metadata.get("active_skill")
    if not isinstance(raw_skill, dict):
        return None
    try:
        return SkillSpec.model_validate(raw_skill)
    except ValueError:
        return None


def _apply_task_strategy(task: TaskState) -> None:
    strategy = select_task_strategy(task)
    if strategy is None:
        task.metadata.pop("strategy", None)
        return
    metadata = strategy.to_metadata()
    task.metadata["strategy"] = metadata
    task.trace_events.append(
        TraceEvent(
            phase="runtime",
            message="Task strategy selected",
            data={
                "strategy": metadata["name"],
                "description": metadata["description"],
                "risk_order": metadata["risk_order"],
            },
        )
    )


def _apply_read_efficiency_limits(task: TaskState) -> None:
    if not _looks_like_broad_code_review_task(task):
        return
    task.limits.max_react_iterations = min(task.limits.max_react_iterations, CODE_REVIEW_MAX_REACT_ITERATIONS)
    task.metadata["read_efficiency_limits"] = {
        "max_react_iterations": task.limits.max_react_iterations,
        "reason": "broad_code_review",
    }


def _looks_like_broad_code_review_task(task: TaskState) -> bool:
    if task.plan and any(step.intent == "code_review" for step in task.plan):
        return True
    normalized = task.goal.lower()
    if any(keyword in normalized for keyword in ["修改", "修复", "新增", "删除", "生成代码", "重构", "测试", "验证"]):
        return False
    return any(keyword in normalized for keyword in ["代码", "源码", "项目", "工程"]) and any(
        keyword in normalized for keyword in ["审查", "问题", "风险", "清单", "质量", "看看", "分析"]
    )


def _normalize_plan_result(result, planner=None) -> tuple[list[PlanStep], str]:
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], str):
        return list(result[0]), result[1]
    return list(result), str(getattr(planner, "last_reasoning_content", "") or "")
