from __future__ import annotations

from time import monotonic

from manus_mini.context import compact_messages_with_snapshot, estimate_message_tokens, validate_tool_call_pairs
from manus_mini.llm import LLMClient, LLMRequestError, LLMResult, extract_usage, get_default_llm_client, openai_messages
from manus_mini.executor import Executor, sanitize_tool_args
from manus_mini.logging import EventLogger
from manus_mini.models import Message, SessionState, TaskState, TraceEvent
from manus_mini.observer import Observer
from manus_mini.scheduler import ToolScheduler
from manus_mini.tools.base import ToolResult
from manus_mini.tools.registry import ToolRegistry


def format_tool_result_message(tool_result) -> str:
    parts = [tool_result.summary]
    if tool_result.paths:
        parts.append("paths:\n" + "\n".join(tool_result.paths))
    if tool_result.written_path:
        parts.append(f"written_path: {tool_result.written_path}")
    if tool_result.content:
        parts.append("content:\n" + tool_result.content)
    if tool_result.error_code:
        parts.append(f"error_code: {tool_result.error_code}")
    return "\n\n".join(parts)


def assistant_message_from_llm_result(llm_result) -> Message:
    message = Message.agent(
        llm_result.content,
        tool_call_ids=[call.id for call in llm_result.tool_calls],
    )
    message.metadata["tool_call_names"] = {call.id: call.name for call in llm_result.tool_calls}
    message.metadata["tool_call_arguments"] = {
        call.id: llm_result.tool_call_arguments.get(call.id, "{}")
        for call in llm_result.tool_calls
    }
    if llm_result.reasoning_content:
        message.metadata["reasoning_content"] = llm_result.reasoning_content
    return message


class ReActLoop:
    def __init__(
        self,
        llm: LLMClient | None = None,
        registry: ToolRegistry | None = None,
        dry_run: bool = False,
        logger: EventLogger | None = None,
    ) -> None:
        self.llm = llm or get_default_llm_client()
        self.registry = registry or ToolRegistry()
        self.scheduler = ToolScheduler(self.registry)
        self.dry_run = dry_run
        self.executor = Executor(self.registry, dry_run=dry_run)
        self.observer = Observer()
        self.logger = logger

    def run(self, task: TaskState, session: SessionState) -> str:
        messages = [
            Message.system(
                "你是本地项目分析 Agent。用户要求了解当前项目时，必须先使用 list_files 查看项目结构，"
                "再用 read_file 读取 README、pyproject 或 docs 中的关键设计文档，最后用中文总结项目作用。"
            )
        ]
        messages.extend(self._conversation_context(task, session))

        for iteration_index in range(1, task.limits.max_react_iterations + 1):
            task.trace_events.append(
                TraceEvent(
                    phase="react",
                    message=f"ReAct iteration {iteration_index} started",
                    data={"iteration": iteration_index, "message_count": len(messages)},
                )
            )
            validate_tool_call_pairs(messages)
            llm_result = self._complete_with_rule_fallback(messages, task, session.session_id)
            llm_result = self._normalize_tool_call_ids(llm_result, iteration_index)
            task.trace_events.append(
                TraceEvent(
                    phase="llm",
                    message=(
                        "LLM returned final content"
                        if not llm_result.tool_calls
                        else f"LLM requested {len(llm_result.tool_calls)} tool call(s)"
                    ),
                    data={
                        "iteration": iteration_index,
                        "content_preview": llm_result.content[:500],
                        "tool_calls": [
                            {
                                "id": call.id,
                                "name": call.name,
                                "args": sanitize_tool_args(call.args),
                                "depends_on": call.depends_on,
                            }
                            for call in llm_result.tool_calls
                        ],
                    },
                )
            )

            if not llm_result.tool_calls:
                self._record_llm_usage(task, llm_result)
                return llm_result.content

            self._record_llm_usage(task, llm_result)
            messages.append(assistant_message_from_llm_result(llm_result))
            session.messages.append(assistant_message_from_llm_result(llm_result))
            known_tool_calls, tool_results = self._prepare_tool_calls(llm_result.tool_calls, task, session)
            known_ids = {call.id for call in known_tool_calls}
            schedulable_calls = [
                call.model_copy(update={"depends_on": [dependency for dependency in call.depends_on if dependency in known_ids]})
                for call in known_tool_calls
            ]
            batches = self.scheduler.plan(schedulable_calls) if schedulable_calls else []
            task.trace_events.append(
                TraceEvent(
                    phase="tool",
                    message=f"Tool scheduler planned {len(batches)} batch(es)",
                    data={
                        "iteration": iteration_index,
                        "batches": [[call.id for call in batch] for batch in batches],
                    },
                )
            )
            for batch in batches:
                tool_results.update(self._run_batch(batch, session, task))

            for call in llm_result.tool_calls:
                tool_result = tool_results[call.id]
                event_data = self._tool_event_data(iteration_index, call, tool_result)
                task.trace_events.append(
                    TraceEvent(
                        phase="tool",
                        message=f"Tool {call.name} finished: {'ok' if tool_result.ok else 'failed'}",
                        data=event_data,
                    )
                )
                task.observations.append(
                    self.observer.observe(call, tool_result)
                )
                tool_message = Message.tool(format_tool_result_message(tool_result), tool_call_id=call.id)
                messages.append(tool_message)
                session.messages.append(tool_message)

            if any(result.error_code in {"WRITE_REQUIRES_CONFIRMATION", "DRY_RUN"} for result in tool_results.values()):
                pending = session.pending_confirmation
                if pending is not None:
                    return pending.prompt or pending.summary or "需要用户确认写入"
                return "需要用户确认写入"

        task.trace_events.append(
            TraceEvent(
                phase="react",
                message="ReAct iteration limit reached",
                data={"max_react_iterations": task.limits.max_react_iterations},
            )
        )
        raise RuntimeError("MAX_REACT_ITERATIONS_REACHED")

    def _record_llm_usage(self, task: TaskState, llm_result: LLMResult) -> None:
        usage = extract_usage(llm_result.source_response)
        if usage is None:
            return
        task.last_prompt_tokens = usage.get("prompt_tokens")
        task.last_completion_tokens = usage.get("completion_tokens")
        task.last_total_tokens = usage.get("total_tokens")

    def _conversation_context(self, task: TaskState, session: SessionState) -> list[Message]:
        history = list(session.messages)
        if not history or history[-1].role != "user" or history[-1].content != task.goal:
            history.append(Message.user(task.goal))
        original_estimated = estimate_message_tokens(history)
        context_limit = self._context_limit(task)
        original_usage = original_estimated / context_limit if context_limit else 1.0
        effective_budget = context_limit
        if original_usage >= 0.90:
            effective_budget = max(1, int(context_limit * 0.55))
        elif original_usage >= 0.70:
            effective_budget = max(1, int(context_limit * 0.69))

        compacted, snapshot = compact_messages_with_snapshot(history, token_budget=effective_budget)
        if (
            original_estimated > context_limit * 2
            and self._estimated_context_tokens(compacted) > context_limit
        ):
            task.trace_events.append(
                TraceEvent(
                    phase="runtime",
                    message="Context token budget exceeded",
                    data={"max_estimated_tokens": task.limits.max_estimated_tokens, "model_context_limit": context_limit},
                )
            )
            raise RuntimeError("TOKEN_BUDGET_EXCEEDED")
        if snapshot is not None:
            session.compression_snapshots.append(snapshot)
            session.messages.append(Message.system(f"[System] 已压缩较早的上下文：{snapshot.summary}"))
        return compacted

    def _context_limit(self, task: TaskState) -> int:
        return max(1, task.model_context_limit or task.limits.max_estimated_tokens)

    def _tool_event_data(self, iteration_index: int, call, tool_result: ToolResult) -> dict:
        data = {
            "iteration": iteration_index,
            "tool_call_id": call.id,
            "tool_name": call.name,
            "args": sanitize_tool_args(call.args),
            "ok": tool_result.ok,
            "summary": tool_result.summary,
            "error_code": tool_result.error_code,
        }
        if call.name == "read_file" and tool_result.ok:
            data["content_omitted"] = True
            return data
        if tool_result.content:
            data["content_preview"] = tool_result.content[:500]
        return data

    def _estimated_context_tokens(self, messages: list[Message]) -> int:
        return estimate_message_tokens(messages)

    def _complete_with_rule_fallback(self, messages: list[Message], task: TaskState, session_id: str) -> LLMResult:
        try:
            request_payload = {"messages": self._loggable_messages(messages), "tool_names": self.registry.names()}
            if self.logger is not None:
                self.logger.record(
                    session_id,
                    task.run_id,
                    {
                        "type": "llm_request",
                        "iteration": len([event for event in task.trace_events if event.phase == "react"]),
                        "request": request_payload,
                    },
                )
            result = self.llm.complete_with_tools(messages, self.registry.names())
            if self.logger is not None:
                self.logger.record(
                    session_id,
                    task.run_id,
                    {
                        "type": "llm_response",
                        "iteration": len([event for event in task.trace_events if event.phase == "react"]),
                        "request": result.source_request or request_payload,
                        "response": result.source_response or result.model_dump(mode="json"),
                    },
                )
            return result
        except (LLMRequestError, ValueError, TypeError, KeyError, IndexError) as error:
            if self.logger is not None:
                self.logger.record(
                    session_id,
                    task.run_id,
                    {
                        "type": "llm_response",
                        "iteration": len([event for event in task.trace_events if event.phase == "react"]),
                        "request": {"messages": self._loggable_messages(messages), "tool_names": self.registry.names()},
                        "response": {"error": str(error) or error.__class__.__name__, "fallback": True},
                    },
                )
            task.trace_events.append(
                TraceEvent(
                    phase="llm",
                    message="LLM returned invalid output, falling back to rule-based draft",
                    data={
                        "error": str(error) or error.__class__.__name__,
                        "tool_names": self.registry.names(),
                    },
                )
            )
            fallback_text = self._rule_fallback_content(task, messages, reason=str(error) or error.__class__.__name__)
            return LLMResult(content=fallback_text)

    def _loggable_messages(self, messages: list[Message]) -> list[dict]:
        return openai_messages(messages)

    def _rule_fallback_content(self, task: TaskState, messages: list[Message], reason: str = "") -> str:
        user_messages = [message.content for message in messages if message.role == "user"]
        focus = user_messages[-1] if user_messages else task.goal
        successful_observations = [observation for observation in task.observations if observation.ok]
        if successful_observations:
            lines = [
                "已使用规则兜底生成草稿：",
                f"- 兜底原因：{reason or 'LLM 返回异常'}",
                f"- 当前目标：{focus}",
                "- 最近工具结果：",
            ]
            for observation in successful_observations[-5:]:
                snippet = observation.content.strip().splitlines()[0] if observation.content.strip() else observation.summary
                lines.append(f"  - {observation.tool_call_id or observation.id}: {observation.summary}。{snippet[:160]}")
            return "\n".join(lines)
        return "\n".join(
            [
                "已使用规则兜底生成草稿：",
                f"- 兜底原因：{reason or 'LLM 返回异常'}",
                f"- 当前目标：{focus}",
            ]
        )

    def _normalize_tool_call_ids(self, llm_result, iteration_index: int):
        seen: set[str] = set()
        tool_calls = []
        tool_call_arguments: dict[str, str] = {}
        for index, call in enumerate(llm_result.tool_calls):
            original_id = call.id
            tool_call_id = original_id or f"call-{iteration_index}-{index}"
            if tool_call_id in seen:
                tool_call_id = f"{tool_call_id}-{index}"
            seen.add(tool_call_id)
            tool_calls.append(call.model_copy(update={"id": tool_call_id}))
            tool_call_arguments[tool_call_id] = llm_result.tool_call_arguments.get(original_id, "{}")

        return llm_result.model_copy(
            update={
                "tool_calls": tool_calls,
                "tool_call_arguments": tool_call_arguments,
            }
        )

    def _prepare_tool_calls(
        self,
        tool_calls,
        task: TaskState,
        session: SessionState,
    ) -> tuple[list, dict[str, ToolResult]]:
        known_tool_calls = []
        tool_results: dict[str, ToolResult] = {}
        for call in tool_calls:
            if call.name in self.registry:
                known_tool_calls.append(self._with_runtime_tool_args(call, session))
                continue
            tool_results[call.id] = ToolResult(
                tool_name=call.name,
                ok=False,
                summary=f"unknown tool: {call.name}",
                error_code="UNKNOWN_TOOL",
            )
            task.trace_events.append(
                TraceEvent(
                    phase="tool",
                    message="Tool call rejected: unknown tool",
                    data={"tool_call_id": call.id, "tool_name": call.name},
                )
            )
        return known_tool_calls, tool_results

    def _with_runtime_tool_args(self, call, session: SessionState):
        return self.executor.prepare_tool_call(call, session)

    def _run_batch(self, batch, session: SessionState, task: TaskState) -> dict[str, ToolResult]:
        batch_started = monotonic()
        result = self.executor.run_batch(batch, session, task)
        self._record_batch_trace(task, batch, batch_started)
        return result

    def _record_batch_trace(self, task: TaskState, batch, batch_started: float) -> None:
        task.trace_events.append(
            TraceEvent(
                phase="tool",
                message="Tool batch completed",
                data={
                    "batch_id": batch[0].id if batch else "empty",
                    "parallel": len(batch) > 1,
                    "tool_call_ids": [call.id for call in batch],
                    "duration_ms": int((monotonic() - batch_started) * 1000),
                },
            )
        )
