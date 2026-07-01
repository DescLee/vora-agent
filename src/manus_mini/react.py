from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from manus_mini.context import compact_messages, validate_tool_call_pairs
from manus_mini.llm import LLMClient, get_default_llm_client
from manus_mini.models import Message, Observation, SessionState, TaskState, TraceEvent
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


RESERVED_TOOL_ARG_NAMES = {"workspace"}


def sanitize_tool_args(args: dict) -> dict:
    return {key: value for key, value in dict(args).items() if key not in RESERVED_TOOL_ARG_NAMES}


class ReActLoop:
    def __init__(self, llm: LLMClient | None = None, registry: ToolRegistry | None = None) -> None:
        self.llm = llm or get_default_llm_client()
        self.registry = registry or ToolRegistry()
        self.scheduler = ToolScheduler(self.registry)

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
            llm_result = self.llm.complete_with_tools(messages, self.registry.names())
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
                return llm_result.content

            messages.append(assistant_message_from_llm_result(llm_result))
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
                task.trace_events.append(
                    TraceEvent(
                        phase="tool",
                        message=f"Tool {call.name} finished: {'ok' if tool_result.ok else 'failed'}",
                        data={
                            "iteration": iteration_index,
                            "tool_call_id": call.id,
                            "tool_name": call.name,
                            "args": sanitize_tool_args(call.args),
                            "ok": tool_result.ok,
                            "summary": tool_result.summary,
                            "error_code": tool_result.error_code,
                            "content_preview": tool_result.content[:500],
                        },
                    )
                )
                task.observations.append(
                    Observation(
                        tool_call_id=call.id,
                        ok=tool_result.ok,
                        summary=tool_result.summary,
                        content=tool_result.content,
                    )
                )
                messages.append(Message.tool(format_tool_result_message(tool_result), tool_call_id=call.id))

        task.trace_events.append(
            TraceEvent(
                phase="react",
                message="ReAct iteration limit reached",
                data={"max_react_iterations": task.limits.max_react_iterations},
            )
        )
        raise RuntimeError("MAX_REACT_ITERATIONS_REACHED")

    def _conversation_context(self, task: TaskState, session: SessionState) -> list[Message]:
        history = list(session.messages)
        if not history or history[-1].role != "user" or history[-1].content != task.goal:
            history.append(Message.user(task.goal))
        return compact_messages(history, token_budget=task.limits.max_estimated_tokens)

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
        run_args = sanitize_tool_args(call.args)
        run_args["workspace"] = session.cwd
        if call.name == "write_file" and "confirmed" not in run_args:
            run_args["confirmed"] = True
        return call.model_copy(update={"args": run_args})

    def _run_batch(self, batch, session: SessionState, task: TaskState) -> dict[str, ToolResult]:
        if len(batch) == 1:
            call = batch[0]
            return {call.id: self._run_tool_call(call, session, task)}

        with ThreadPoolExecutor(max_workers=len(batch)) as executor:
            results = executor.map(lambda call: (call.id, self._run_tool_call(call, session, task)), batch)
            return dict(results)

    def _run_tool_call(self, call, session: SessionState, task: TaskState) -> ToolResult:
        max_attempts = max(1, task.limits.max_tool_retries + 1)
        last_result: ToolResult | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                tool = self.registry.get(call.name)
                result = tool.run(**call.args)
            except PermissionError as error:
                return ToolResult(
                    tool_name=call.name,
                    ok=False,
                    summary=str(error) or error.__class__.__name__,
                    error_code=str(error) or "PERMISSION_DENIED",
                )
            except Exception as error:  # noqa: BLE001
                result = ToolResult(
                    tool_name=call.name,
                    ok=False,
                    summary=str(error) or error.__class__.__name__,
                    error_code="TOOL_ERROR",
                )

            if result.ok:
                return result

            last_result = result
            if attempt < max_attempts:
                task.trace_events.append(
                    TraceEvent(
                        phase="tool",
                        message="Tool retry scheduled",
                        data={
                            "tool_call_id": call.id,
                            "tool_name": call.name,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "error_code": result.error_code,
                        },
                    )
                )

        assert last_result is not None
        return last_result.model_copy(update={"error_code": "TOOL_RETRY_EXHAUSTED"})
