from __future__ import annotations

import json
import re
import shlex
from collections.abc import Sequence
from pathlib import PurePosixPath
from time import monotonic

from manus_mini.context import compact_messages_with_snapshot, estimate_message_tokens, run_context_compression_pipeline, validate_tool_call_pairs
from manus_mini.llm import (
    LLMClient,
    LLMRequestError,
    LLMResult,
    OpenAICompatibleLLMClient,
    extract_usage,
    get_default_llm_client,
    openai_messages,
)
from manus_mini.executor import Executor, sanitize_tool_args
from manus_mini.logging import EventLogger
from manus_mini.models import Message, SessionState, TaskState, TraceEvent
from manus_mini.observer import Observer
from manus_mini.scheduler import ToolScheduler
from manus_mini.tools.base import ToolResult
from manus_mini.tools.registry import ToolRegistry
from manus_mini.tools.shell_tools import LLMCommandRiskJudge, RunBashTool, RunTempScriptTool


MAX_TOOL_RESULT_PATHS = 20
MAX_TOOL_RESULT_CONTENT_CHARS = 4000
RUNTIME_ARG_KEYS = {"workspace", "confirmed"}
REPEAT_CACHEABLE_TOOLS = {"list_files", "read_file"}
REPEAT_BLOCKED_TOOLS = {"write_file", "replace_in_file", "append_file", "make_directory", "run_bash", "run_temp_script"}
CODE_WRITE_TOOLS = {"write_file", "replace_in_file", "append_file"}
SHELL_CODE_WRITE_TOOLS = {"run_bash", "run_temp_script"}
CODE_FILE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".swift",
    ".ts",
    ".tsx",
}
OVERVIEW_GOAL_KEYWORDS = (
    "优化建议",
    "建议",
    "想法",
    "看下",
    "看看",
    "分析一下",
    "了解",
    "说明",
    "总结",
)

IDENTITY_GOAL_KEYWORDS = ("你是谁", "你的名字", "你叫什么")
STARTUP_GOAL_KEYWORDS = ("怎么启动", "如何启动", "怎么运行", "如何运行", "怎么使用", "如何使用")
UNAVAILABLE_GOAL_KEYWORDS = ("模型不可用", "llm 不可用", "llm不可用", "模型挂了", "模型异常")
CAPABILITY_GOAL_KEYWORDS = ("核心能力", "主要能力", "功能亮点", "项目亮点")
SECURITY_GOAL_KEYWORDS = ("怎么保证安全", "安全", "安全边界", "权限")
REFLECTION_GATE_GOAL_KEYWORDS = ("reflection", "质量门禁", "pytest gate", "反思")
TESTING_GOAL_KEYWORDS = ("测试怎么跑", "怎么测试", "如何测试", "验证怎么跑", "质量门禁")
FILE_EDIT_GOAL_KEYWORDS = ("怎么修改文件", "如何修改文件", "改文件", "修改文件")
WRITE_CONFIRMATION_GOAL_KEYWORDS = ("写入确认", "确认写入", "人工确认", "确认流")
WORKSPACE_BOUNDARY_GOAL_KEYWORDS = ("工作区外", "越界", "workspace 外", "cwd 外")
CODE_QUALITY_GOAL_KEYWORDS = ("改代码", "改完是对的", "代码修改", "代码质量")
RESUME_SESSION_GOAL_KEYWORDS = ("恢复刚才", "恢复会话", "继续修改", "继续会话")
SESSION_LIST_GOAL_KEYWORDS = ("查看历史会话", "历史会话", "会话列表", "查看会话")
MODEL_CONFIG_GOAL_KEYWORDS = ("配置模型", "模型配置", "怎么配置模型", "llm 配置", "llm配置")
OUTPUT_LOCATION_GOAL_KEYWORDS = ("日志", "产物保存", "保存在哪里", "输出在哪里", "运行产物")
CONTEXT_COMPRESSION_GOAL_KEYWORDS = ("上下文压缩", "压缩上下文", "context compression")
MEMORY_GOAL_KEYWORDS = ("长期记忆", "记忆是怎么", "memory")
SEARCH_FAILURE_GOAL_KEYWORDS = ("搜索失败", "联网失败", "搜索不到", "页面内容读取失败")
DRY_RUN_GOAL_KEYWORDS = ("dry-run", "dry run", "试运行", "预演")
COMMAND_RISK_GOAL_KEYWORDS = ("命令执行", "命令风险", "控制风险", "危险命令", "shell 风险")
TOOL_SCHEDULER_GOAL_KEYWORDS = ("工具并行", "并行调度", "工具调度", "scheduler")
EVAL_GOAL_KEYWORDS = ("eval", "评测", "评估怎么跑")
ARCHITECTURE_GOAL_KEYWORDS = ("架构怎么讲", "架构", "模块关系", "设计怎么讲")
PACKAGE_BUILD_GOAL_KEYWORDS = ("打包发布", "怎么打包", "构建包", "发布")
PROJECT_BOUNDARY_GOAL_KEYWORDS = ("边界和不足", "项目边界", "不足", "局限")
MANUS_GAP_GOAL_KEYWORDS = ("真正的manus", "真正的 manus", "manus 比", "和manus")
DEMO_GOAL_KEYWORDS = ("怎么演示", "如何演示", "面试演示", "演示这个项目")
TOOL_EXTENSION_GOAL_KEYWORDS = ("扩展一个新工具", "新增工具", "加一个工具", "扩展工具")
PRODUCTION_READINESS_GOAL_KEYWORDS = ("生产化", "生产级", "上线还缺", "还缺什么")
TROUBLESHOOTING_GOAL_KEYWORDS = ("怎么排障", "如何排障", "出问题", "定位问题")
SENIORITY_GOAL_KEYWORDS = ("8年经验", "八年经验", "高级工程师", "资深")
REPORT_GOAL_KEYWORDS = ("行研", "研究", "调研", "摘要", "总结", "报告")
EXPLICIT_WRITE_INTENT_KEYWORDS = (
    "保存到",
    "写到",
    "输出到",
    "放到",
    "写入文件",
    "生成文件",
    "落到文件",
    "保存成",
    "输出成",
    "写入 ",
    "创建文件",
    "新建文件",
)


def format_tool_result_message(tool_result) -> str:
    parts = [tool_result.summary]
    if tool_result.paths:
        parts.append(_format_paths(tool_result.paths))
    if tool_result.written_path:
        parts.append(f"written_path: {tool_result.written_path}")
    if tool_result.content:
        parts.append(_format_content(tool_result.content))
    elif tool_result.ok:
        parts.append("content:\n[empty]")
    if tool_result.error_code:
        parts.append(f"error_code: {tool_result.error_code}")
    return "\n\n".join(parts)


def _format_paths(paths: list[str]) -> str:
    visible_paths = paths[:MAX_TOOL_RESULT_PATHS]
    lines = ["paths:"]
    lines.extend(visible_paths)
    extra = len(paths) - len(visible_paths)
    if extra > 0:
        lines.append(f"... [truncated {extra} more path(s)]")
    return "\n".join(lines)


def _format_content(content: str) -> str:
    if len(content) <= MAX_TOOL_RESULT_CONTENT_CHARS:
        return "content:\n" + content
    truncated = content[:MAX_TOOL_RESULT_CONTENT_CHARS]
    remaining = len(content) - MAX_TOOL_RESULT_CONTENT_CHARS
    return "\n".join(
        [
            "content:",
            truncated,
            f"... [truncated {remaining} more char(s)]",
        ]
    )


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
        self.llm = llm
        self.registry = registry or ToolRegistry()
        self._attach_command_risk_judge()
        self.scheduler = ToolScheduler(self.registry)
        self.dry_run = dry_run
        self.executor = Executor(self.registry, dry_run=dry_run)
        self.observer = Observer()
        self.logger = logger

    def _attach_command_risk_judge(self) -> None:
        if self.llm is None:
            return
        supports_risk_judgement = isinstance(self.llm, OpenAICompatibleLLMClient) or bool(
            getattr(self.llm, "supports_command_risk_judgement", False)
        )
        if not supports_risk_judgement:
            return
        risk_judge = LLMCommandRiskJudge(self.llm)
        for tool in self.registry.all():
            if isinstance(tool, (RunBashTool, RunTempScriptTool)) and tool.risk_judge is None:
                tool.risk_judge = risk_judge

    def run(self, task: TaskState, session: SessionState) -> str:
        system_prompt = self._system_prompt_for_task(task)
        tool_names = [] if self._is_chat_only_task(task) else self.registry.names()
        messages = [
            Message.system(system_prompt)
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
            llm_result = self._complete_with_rule_fallback(messages, task, session.session_id, tool_names=tool_names)
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
                        "reasoning_content": llm_result.reasoning_content[:1000],
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
                return self._finalize_answer_content(task, llm_result.content)

            self._record_llm_usage(task, llm_result)
            assistant_message = assistant_message_from_llm_result(llm_result)
            messages.append(assistant_message)
            session.messages.append(assistant_message)
            self._compress_session_context_if_needed(task, session, trigger_stage="after_llm_message")
            known_tool_calls, tool_results = self._prepare_tool_calls(llm_result.tool_calls, task, session)
            executable_call_by_id = {call.id: call for call in known_tool_calls}
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
                event_call = executable_call_by_id.get(call.id, call)
                event_data = self._tool_event_data(iteration_index, event_call, tool_result)
                if self.logger is not None:
                    self.logger.record(
                        session.session_id,
                        task.run_id,
                        {
                            "type": "tool_result",
                            "stage": "tool",
                            "iteration": iteration_index,
                            "tool_call_id": call.id,
                            "tool_name": call.name,
                            "args": sanitize_tool_args(event_call.args),
                            "ok": tool_result.ok,
                            "result": tool_result.model_dump(mode="json"),
                        },
                    )
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
            if self._code_change_validated_after_latest_write(task):
                return self._force_final_answer(
                    messages,
                    task,
                    session,
                    fallback="代码修改已完成，测试已通过。",
                    trace_message="Code change validated; forcing final answer",
                    trace_data={"iteration": iteration_index, "reason": "code_change_validated"},
                    system_instruction="代码修改已经完成，且最近一次代码修改后的测试已通过。请直接输出最终结果，不要再请求任何工具。",
                    llm_trace_message="LLM forced final answer after validated code change",
                )

        return self._force_final_answer(messages, task, session)

    def _is_chat_only_task(self, task: TaskState) -> bool:
        if _goal_mentions_current_project(task.goal):
            return False
        return bool(task.plan) and all(step.intent == "chat" for step in task.plan)

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

        compacted, snapshot = compact_messages_with_snapshot(history, token_budget=effective_budget, llm=self.llm)
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
        return compacted

    def _context_limit(self, task: TaskState) -> int:
        return max(1, task.model_context_limit or task.limits.max_estimated_tokens)

    def _compress_session_context_if_needed(self, task: TaskState, session: SessionState, trigger_stage: str) -> None:
        context_limit = self._context_limit(task)
        result = run_context_compression_pipeline(
            session.messages,
            token_limit=context_limit,
            trigger_stage=trigger_stage,
            llm=self.llm,
        )
        if not result.applied_strategies:
            return
        self._record_context_compression(task, session, result, context_limit)
        session.messages = list(result.messages)
        session.compression_snapshots.extend(result.snapshots)
        if not (session.messages and session.messages[-1].role == "agent" and session.messages[-1].tool_call_ids):
            session.messages.append(
                Message.system(
                    "[System] 已压缩较早的上下文："
                    f"压缩前估算 {result.before_tokens} tokens，目标预算 {max(1, int(context_limit * 0.50))} tokens，阈值 50%，"
                    f"压缩后估算 {result.after_tokens} tokens，保留消息 {len(result.messages)} 条，"
                    f"策略 {', '.join(result.applied_strategies)}。"
                )
            )

    def _record_context_compression(self, task: TaskState, session: SessionState, result, context_limit: int) -> None:  # noqa: ANN001
        started_data = {
            "message_type": "context_compression_started",
            "trigger_stage": result.trigger_stage,
            "estimated_tokens": result.before_tokens,
            "context_limit": context_limit,
            "trigger_usage_percent": (result.before_usage or 0) * 100,
            "compression_target": _compression_target_label(result.applied_strategies),
            "covered_message_count": sum(len(snapshot.covered_message_ids) for snapshot in result.snapshots),
            "strategies": list(result.applied_strategies),
            "threshold": 0.50,
        }
        completed_data = {
            "message_type": "context_compression_completed",
            "trigger_stage": result.trigger_stage,
            "estimated_tokens": result.before_tokens,
            "context_limit": context_limit,
            "compacted_tokens": result.after_tokens,
            "covered_message_count": sum(len(snapshot.covered_message_ids) for snapshot in result.snapshots),
            "retained_fact_count": sum(len(snapshot.retained_facts) for snapshot in result.snapshots),
            "strategies": list(result.applied_strategies),
            "snapshot_id": result.snapshots[-1].id if result.snapshots else None,
            "summary_source": result.snapshots[-1].metadata.get("summary_source", "rule") if result.snapshots else "rule",
            "threshold": 0.50,
        }
        task.trace_events.append(TraceEvent(phase="runtime", message="Context compression started", data=started_data))
        task.trace_events.append(TraceEvent(phase="runtime", message="Context compression completed", data=completed_data))
        if self.logger is not None:
            self.logger.record(
                session.session_id,
                task.run_id,
                {
                    "type": "context_compression_started",
                    "trigger_stage": result.trigger_stage,
                    "before_tokens": result.before_tokens,
                    "before_usage": result.before_usage,
                    "context_limit": context_limit,
                    "strategies": list(result.applied_strategies),
                    "threshold": 0.50,
                },
            )
            self.logger.record(
                session.session_id,
                task.run_id,
                {
                    "type": "context_compression_completed",
                    "trigger_stage": result.trigger_stage,
                    "before_tokens": result.before_tokens,
                    "after_tokens": result.after_tokens,
                    "before_usage": result.before_usage,
                    "after_usage": result.after_usage,
                    "context_limit": context_limit,
                    "strategies": list(result.applied_strategies),
                    "covered_message_count": completed_data["covered_message_count"],
                    "compressed_chars": sum(int(snapshot.metadata.get("compressed_chars", 0)) for snapshot in result.snapshots),
                    "summary_source": completed_data["summary_source"],
                    "snapshot_id": completed_data["snapshot_id"],
                    "threshold": 0.50,
                },
            )

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
        fingerprint = _tool_call_fingerprint(call)
        if fingerprint is not None:
            data["fingerprint"] = fingerprint
        if call.args.get("_path_rewritten") is True:
            data["path_rewritten"] = True
        if tool_result.data.get("deduplicated"):
            data["deduplicated"] = True
            data["source_tool_call_id"] = tool_result.data.get("source_tool_call_id")
            data["fingerprint"] = tool_result.data.get("fingerprint") or fingerprint
        if tool_result.data.get("blocked_duplicate"):
            data["blocked_duplicate"] = True
            data["source_tool_call_id"] = tool_result.data.get("source_tool_call_id")
            data["fingerprint"] = tool_result.data.get("fingerprint") or fingerprint
        if call.name in {"run_bash", "run_temp_script"}:
            data["exit_code"] = tool_result.data.get("exit_code")
            data["stdout"] = tool_result.data.get("stdout", "")[:500]
            data["stderr"] = tool_result.data.get("stderr", "")[:500]
            data["timed_out"] = tool_result.data.get("timed_out", False)
        if call.name == "read_file" and tool_result.ok:
            data["content_omitted"] = True
            return data
        if tool_result.content:
            data["content_preview"] = tool_result.content[:500]
        return data

    def _estimated_context_tokens(self, messages: list[Message]) -> int:
        return estimate_message_tokens(messages)

    def _code_change_validated_after_latest_write(self, task: TaskState) -> bool:
        latest_write_index = -1
        for index, event in enumerate(task.trace_events):
            if event.phase != "tool":
                continue
            data = event.data
            if data.get("tool_name") in {"write_file", "replace_in_file", "append_file", "make_directory"} and data.get("ok") is True:
                latest_write_index = index
        if latest_write_index < 0:
            return False
        has_passing_test = False
        for event in task.trace_events[latest_write_index + 1:]:
            if event.phase != "tool":
                continue
            data = event.data
            if _tool_event_failed(data):
                return False
            if not _is_test_tool_event(data):
                continue
            if not _test_tool_event_passed(data):
                return False
            has_passing_test = True
        return has_passing_test

    def _force_final_answer(
        self,
        messages: list[Message],
        task: TaskState,
        session: SessionState,
        fallback: str | None = None,
        trace_message: str = "ReAct iteration limit reached; forcing final answer",
        trace_data: dict | None = None,
        system_instruction: str = "已达到工具循环上限。请基于现有上下文直接输出最终答案，不要再请求任何工具。",
        llm_trace_message: str = "LLM forced final answer after ReAct limit",
    ) -> str:
        task.trace_events.append(
            TraceEvent(
                phase="react",
                message=trace_message,
                data=trace_data or {"max_react_iterations": task.limits.max_react_iterations},
            )
        )
        final_messages = [
            *messages,
            Message.system(system_instruction),
        ]
        llm_result = self._complete_with_rule_fallback(final_messages, task, session.session_id)
        llm_result = self._normalize_tool_call_ids(llm_result, task.limits.max_react_iterations + 1)
        task.trace_events.append(
            TraceEvent(
                phase="llm",
                message=llm_trace_message,
                data={
                    "iteration": task.limits.max_react_iterations + 1,
                    "content_preview": llm_result.content[:500],
                    "tool_calls": [],
                },
            )
        )
        self._record_llm_usage(task, llm_result)
        if llm_result.tool_calls:
            task.trace_events.append(
                TraceEvent(
                    phase="runtime",
                    message="Forced final answer ignored tool calls",
                    data={"tool_call_count": len(llm_result.tool_calls)},
                )
            )
        content = llm_result.content or fallback or "已达到工具循环上限，保留当前最佳结果。"
        return self._finalize_answer_content(task, content)

    def _complete_with_rule_fallback(
        self,
        messages: list[Message],
        task: TaskState,
        session_id: str,
        tool_names: list[str] | None = None,
    ) -> LLMResult:
        try:
            resolved_tool_names = tool_names if tool_names is not None else self.registry.names()
            request_payload = {"messages": self._loggable_messages(messages), "tool_names": resolved_tool_names}
            if self.logger is not None:
                self.logger.record(
                    session_id,
                    task.run_id,
                    {
                        "type": "llm_request",
                        "stage": "react",
                        "iteration": len([event for event in task.trace_events if event.phase == "react"]),
                        "request": request_payload,
                        "api_request_payload": request_payload,
                    },
                )
            result = self._resolve_llm().complete_with_tools(messages, resolved_tool_names)
            api_request_payload = result.source_request or request_payload
            api_response_raw = result.source_response or result.model_dump(mode="json")
            if self.logger is not None:
                self.logger.record(
                    session_id,
                    task.run_id,
                    {
                        "type": "llm_response",
                        "stage": "react",
                        "iteration": len([event for event in task.trace_events if event.phase == "react"]),
                        "request": api_request_payload,
                        "response": api_response_raw,
                        "api_request_payload": api_request_payload,
                        "api_response_raw": api_response_raw,
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
                        "stage": "react",
                        "iteration": len([event for event in task.trace_events if event.phase == "react"]),
                        "request": {
                            "messages": self._loggable_messages(messages),
                            "tool_names": tool_names if tool_names is not None else self.registry.names(),
                        },
                        "response": {"error": str(error) or error.__class__.__name__, "fallback": True},
                        "api_request_payload": {
                            "messages": self._loggable_messages(messages),
                            "tool_names": tool_names if tool_names is not None else self.registry.names(),
                        },
                        "api_response_raw": {"error": str(error) or error.__class__.__name__, "fallback": True},
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

    def _resolve_llm(self) -> LLMClient:
        if self.llm is None:
            self.llm = get_default_llm_client()
        return self.llm

    def _system_prompt_for_task(self, task: TaskState) -> str:
        if self._is_chat_only_task(task):
            return (
                "你叫 manus-mini，是用户的个人助理。"
                "你专门负责代码项目的查看、总结、诊断和优化建议；"
                "也具备代码能力，可以对项目代码进行查看、总结、修改、删除和生成。"
                "除此以外，你还可以进行文档写作、文档总结，以及深度行业研究报告撰写。"
                "对于闲聊、问候、名字和自我介绍类轻量问题，请基于这个身份直接回复，不要调用工具。"
            )
        plan_text = self._format_execution_plan(task)
        return "\n".join(
            [
                "你是 manus-mini 的执行阶段 Agent。请遵循 Planner 已制定的计划和已有上下文。",
                f"当前任务：{task.goal}",
                f"当前工作目录：{task.cwd}",
                "用户说“当前项目”“这个项目”“这个工程”时，指的就是当前工作目录；不要要求用户再提供项目描述、链接或代码。",
                "涉及项目或代码时，先利用已有项目结构摘要、当前执行计划和最近工具结果判断；只有信息不足时才调用工具。",
                "开始执行前先定位目标模块和最小相关文件；已有上下文足够时直接推进，不要为了确认而重复读取。",
                "工具调用要尽量少，优先读取少量关键文件，避免重复 list_files/read_file 或无目的全量扫描。",
                "如果用户表达“没想好、你来定、你看着办、反正换个”等授权你代为选择的意思，不要只给选项并等待用户选择；请自行选择保守方案并继续执行。",
                "如果计划要求读取 README、pyproject 或 docs 中的关键文档，请直接使用 read_file 读取对应文件。",
                "没有读取原文件前，不要凭空改写已有文件；需要修改时先确认当前内容和精确替换位置。",
                "修改已有文件时优先使用 replace_in_file 做精确局部替换；只有创建新文件或必须整体重写时才使用 write_file。",
                "如果任务涉及代码修改、修复、生成或删除，开始阶段必须先准备可执行测试命令或临时测试脚本；修改后必须运行测试。",
                "测试脚本优先使用 run_temp_script 并传入 is_test=true，执行完会自动删除；也可以用 run_bash 执行项目已有测试命令。",
                "如果测试失败，必须根据 stdout/stderr 修复后重新运行，直到测试全部通过或达到循环上限。",
                "最终答复要说明改了什么、验证了什么；如果未修改代码，则说明依据和结论，不要输出空泛过程描述。",
                "",
                "当前执行计划",
                plan_text,
            ]
        )

    def _format_execution_plan(self, task: TaskState) -> str:
        if not task.plan:
            return "- 暂无计划，请根据当前任务谨慎判断是否需要工具。"
        lines = []
        for index, step in enumerate(task.plan, start=1):
            lines.append(f"{index}. {step.description} | {step.intent} | {step.status}")
        return "\n".join(lines)

    def _rule_fallback_content(self, task: TaskState, messages: list[Message], reason: str = "") -> str:
        user_messages = [message.content for message in messages if message.role == "user"]
        focus = user_messages[-1] if user_messages else task.goal
        direct_answer = self._direct_fallback_answer(focus)
        if direct_answer:
            return direct_answer
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

    def _direct_fallback_answer(self, focus: str) -> str:
        normalized = focus.strip().lower()
        compact_focus = _compact_cjk_spaces(normalized)
        if any(keyword in normalized for keyword in IDENTITY_GOAL_KEYWORDS):
            return "我是 manus-mini，本地 Agent Runtime，主要用来分析项目、调用工具和协助完成代码与文档任务。"
        if any(keyword in normalized for keyword in STARTUP_GOAL_KEYWORDS):
            return "\n".join(
                [
                    "可以先按这个顺序启动：",
                    "1. 安装依赖：`pip install -e \".[dev]\"`",
                    "2. 配置 `.env` 里的 `LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`",
                    "3. 运行一次任务：`manus-mini run \"总结一下当前项目\" --cwd .`",
                    "4. 查看历史会话：`manus-mini list --cwd .`",
                    "5. 恢复会话：`manus-mini resume <session-id> --cwd .`",
                ]
            )
        if any(keyword in normalized for keyword in UNAVAILABLE_GOAL_KEYWORDS):
            return (
                "如果模型不可用，我会退回规则兜底模式。"
                "理想情况下会直接给出最小可用答案；如果上下文不足，也会明确说明失败原因，而不是输出原始工具调用。"
            )
        if any(keyword in compact_focus for keyword in CAPABILITY_GOAL_KEYWORDS):
            return (
                "核心能力包括：任务规划、ReAct 工具调用、会话持久化、上下文压缩、写入确认、"
                "Reflection 质量门禁和结构化运行日志。面试展示时可以按“目标 -> 计划 -> 工具 -> 验证 -> 会话恢复”这条链路讲。"
            )
        if any(keyword in compact_focus for keyword in SECURITY_GOAL_KEYWORDS):
            return (
                "安全设计主要靠工作区边界、写入确认、命令风险识别、敏感信息脱敏和会话文件路径校验。"
                "文件工具默认限制在 cwd 内，危险命令和写入动作会进入确认或拒绝路径。"
            )
        if any(keyword in compact_focus for keyword in REFLECTION_GATE_GOAL_KEYWORDS):
            return (
                "Reflection 质量门禁位于 ReAct 草稿之后：非代码任务可直接 accept；代码任务会检查测试证据，"
                "必要时生成并运行 pytest gate。结果可能是 accept、local_update、regenerate 或 replan，"
                "没有验证证据的代码修改不会直接通过。"
            )
        if any(keyword in compact_focus for keyword in TESTING_GOAL_KEYWORDS):
            return (
                "建议按这个顺序验证：`pytest -q`、`ruff check src tests evals`、`mypy`、"
                "`python evals/run_evals.py`、`pytest --cov=manus_mini --cov-report=term-missing`、`python -m build`。"
            )
        if any(keyword in compact_focus for keyword in FILE_EDIT_GOAL_KEYWORDS):
            return (
                "修改文件通常是先用 `read_file` 看上下文，再用 `replace_in_file` 做局部替换；"
                "涉及写入时会进入写入确认，确认后才真正落盘。"
            )
        if any(keyword in compact_focus for keyword in WRITE_CONFIRMATION_GOAL_KEYWORDS):
            return "写入确认会展示 diff 和目标路径，用户输入确认才执行；输入取消或其它内容会拒绝本次写入。"
        if any(keyword in compact_focus for keyword in WORKSPACE_BOUNDARY_GOAL_KEYWORDS):
            return "不能修改工作区外的文件。文件工具会校验路径边界，越界访问会返回 `PATH_OUT_OF_WORKSPACE`。"
        if any(keyword in compact_focus for keyword in CODE_QUALITY_GOAL_KEYWORDS):
            return (
                "代码修改会强调先测试再改代码；Reflection 会检查测试证据，必要时运行 pytest gate，"
                "没有验证证据的代码结果不会直接通过。"
            )
        if any(keyword in compact_focus for keyword in RESUME_SESSION_GOAL_KEYWORDS):
            return (
                "先用 `manus-mini list --cwd .` 找到 `session_id`，再执行 "
                "`manus-mini resume <session_id> --cwd .` 继续同一个会话。"
            )
        if any(keyword in compact_focus for keyword in SESSION_LIST_GOAL_KEYWORDS):
            return (
                "用 `manus-mini list --cwd .` 查看历史会话列表；列表里会展示 `session_id`、更新时间、消息数和最近用户问题。"
                "需要继续时执行 `manus-mini resume <session_id> --cwd .`。"
            )
        if any(keyword in compact_focus for keyword in MODEL_CONFIG_GOAL_KEYWORDS):
            return (
                "模型通过 OpenAI-compatible 配置接入。配置读取顺序是环境变量、当前目录 `.env`、"
                "`~/.manus-mini/.env`、源码根目录 `.env`；关键项是 `LLM_PROVIDER`、`LLM_BASE_URL`、"
                "`LLM_API_KEY`、`LLM_MODEL`，超时和重试可用 `LLM_TIMEOUT_SECONDS`、`LLM_MAX_ATTEMPTS` 调整。"
            )
        if any(keyword in compact_focus for keyword in OUTPUT_LOCATION_GOAL_KEYWORDS):
            return (
                "会话和本地状态保存在工作目录对应的 `.manus-mini` 存储下，历史会话在 `sessions`，"
                "结构化运行记录在 `logs`，报告和上下文快照等产物在 `outputs`。"
                "面试时可以用 `manus-mini list --cwd .` 找会话，再看对应日志和输出目录。"
            )
        if any(keyword in compact_focus for keyword in CONTEXT_COMPRESSION_GOAL_KEYWORDS):
            return (
                "上下文压缩按 50% / 70% / 90% 分层处理：先压缩较长工具输出，再摘要历史消息，最后必要时强制截断。"
                "每次压缩会记录 `CompressionSnapshot`，并校验 assistant/tool call/result 成组完整，避免破坏 tool call 上下文。"
            )
        if any(keyword in compact_focus for keyword in MEMORY_GOAL_KEYWORDS):
            return (
                "长期记忆使用 SQLite 存储，项目级路径由 `project_memory_path` 计算；当前实现是关键词检索，"
                "会记录偏好、决策和项目摘要等信息。写入前会过滤敏感信息，避免 API key、token 等进入记忆库。"
            )
        if any(keyword in compact_focus for keyword in SEARCH_FAILURE_GOAL_KEYWORDS):
            return (
                "搜索失败时不会假装拿到了证据：没有搜索结果会说明“未获取到有效搜索结果”，"
                "网页抓取失败会说明“页面内容读取失败”，最终回答应标注证据不足，并建议换关键词或补充可读资料。"
            )
        if any(keyword in compact_focus for keyword in DRY_RUN_GOAL_KEYWORDS):
            return (
                "`--dry-run` 是写入和命令执行的预演模式：工具会返回确认预览和计划动作，但不落盘、不真正执行有副作用操作。"
                "面试时可以用它展示安全边界和确认流。"
            )
        if any(keyword in compact_focus for keyword in COMMAND_RISK_GOAL_KEYWORDS):
            return (
                "命令执行通过 `run_bash` 和 `run_temp_script` 接入，执行前会做命令风险判断。"
                "危险命令、写文件、读取敏感文件或生产代码修改会被拒绝或进入确认流程，普通只读命令才直接执行。"
            )
        if any(keyword in compact_focus for keyword in TOOL_SCHEDULER_GOAL_KEYWORDS):
            return (
                "`ToolScheduler` 会把无依赖的只读工具合并到同一批次并行执行；写入工具、敏感工具和有依赖的工具保持串行，"
                "避免并发写入或先后顺序错误。"
            )
        if any(keyword in compact_focus for keyword in EVAL_GOAL_KEYWORDS):
            return (
                "项目级 eval 用 `python evals/run_evals.py` 运行，当前覆盖 9 个关键约束，包括 Reflection、"
                "tool exchange 完整性、并行调度、写入确认和安全边界。"
            )
        if any(keyword in compact_focus for keyword in ARCHITECTURE_GOAL_KEYWORDS):
            return (
                "架构可以按 `Runtime -> Planner -> ReAct -> ToolScheduler -> Executor/Observer -> Reflection -> Reporter` 讲。"
                "Runtime 负责外层工程循环，Planner 拆任务，ReAct 驱动工具调用，ToolScheduler 调度工具，Reflection 做质量回流。"
            )
        if any(keyword in compact_focus for keyword in PACKAGE_BUILD_GOAL_KEYWORDS):
            return (
                "打包由 `pyproject.toml` 定义，执行 `python -m build` 会生成 sdist 和 wheel，产物在 `dist/`。"
                "提交前还要跑 pytest、ruff、mypy、eval 和覆盖率，保证包构建不是唯一门禁。"
            )
        if any(keyword in compact_focus for keyword in PROJECT_BOUNDARY_GOAL_KEYWORDS):
            return (
                "当前边界要讲清：这是本地单用户、非生产级 Agent Runtime；命令执行不是容器沙箱，"
                "memory 还是 SQLite + 关键词检索而非向量检索，LLM 也还没有 streaming、多 provider 和完整云端权限体系。"
            )
        if any(keyword in compact_focus for keyword in MANUS_GAP_GOAL_KEYWORDS):
            return (
                "它不是完整 Manus。差距主要在浏览器自动化、远程沙箱、多租户账号体系、任务市场、"
                "长周期任务编排和云端可观测平台；本项目聚焦本地 Agent Runtime 的工程骨架。"
            )
        if any(keyword in compact_focus for keyword in DEMO_GOAL_KEYWORDS):
            return (
                "面试演示建议走三段：先做项目分析展示规划和工具读取，再做一次写入确认展示安全边界，"
                "最后讲 Reflection/pytest gate 和会话恢复，说明它不是一次 LLM 调用。"
            )
        if any(keyword in compact_focus for keyword in TOOL_EXTENSION_GOAL_KEYWORDS):
            return (
                "扩展新工具通常是新增工具类、定义 `ToolSpec`、返回 `ToolResult`，再注册到 `ToolRegistry`。"
                "同时补工具单测、调度/风险测试，必要时更新 prompt 或 eval。"
            )
        if any(keyword in compact_focus for keyword in PRODUCTION_READINESS_GOAL_KEYWORDS):
            return (
                "生产化还缺多租户、容器隔离、权限审计、集中可观测性、配额和成本控制、"
                "更严格的密钥托管、任务队列以及真实用户级 SLA。"
            )
        if any(keyword in compact_focus for keyword in TROUBLESHOOTING_GOAL_KEYWORDS):
            return (
                "排障先拿 `session_id`，再看对应 `logs`、会话 JSON、`trace_events` 和 summary。"
                "通常按 LLM 请求、tool call、工具返回、Reflection 决策、最终报告这条链路定位。"
            )
        if any(keyword in compact_focus for keyword in SENIORITY_GOAL_KEYWORDS):
            return (
                "它体现的不是页面复杂度，而是工程边界：有安全边界、可观测日志、会话恢复、上下文压缩、"
                "工具调度、Reflection 质量门禁和测试/eval 门禁，这些是资深工程师会主动补齐的系统能力。"
            )
        if _goal_mentions_current_project(compact_focus) and any(
            keyword in compact_focus for keyword in OVERVIEW_GOAL_KEYWORDS
        ):
            return (
                "这个项目是 manus-mini，一个本地终端里的 Agent 运行框架。"
                "它的重点是任务规划、工具调用、结果验证和会话持久化，适合做 Agent 工程能力展示。"
            )
        return ""

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
        tool_call_count = 0
        planned_read_file_keys: set[tuple[str, str, int | None, int | None]] = set()
        planned_read_fragment_counts: dict[tuple[str, str], int] = {}
        planned_fingerprints: dict[str, str] = {}
        for call in tool_calls:
            fragmented_read = self._fragmented_read_file_result(call, planned_read_fragment_counts)
            if fragmented_read is not None:
                tool_results[call.id] = fragmented_read
                self._record_tool_rejection(task, call, fragmented_read)
                continue
            budget_error = self._tool_budget_error(call, task, tool_call_count)
            if budget_error is not None:
                tool_results[call.id] = budget_error
                self._record_tool_rejection(task, call, budget_error)
                continue
            code_change_error = self._code_change_precondition_error(call, task, session)
            if code_change_error is not None:
                tool_results[call.id] = code_change_error
                self._record_tool_rejection(task, call, code_change_error)
                continue
            report_write_error = self._report_write_precondition_error(call, task)
            if report_write_error is not None:
                tool_results[call.id] = report_write_error
                self._record_tool_rejection(task, call, report_write_error)
                continue
            duplicate_read = self._duplicate_successful_read_file_result(call, task)
            if duplicate_read is not None:
                tool_results[call.id] = duplicate_read
                continue
            duplicate_planned_read = self._duplicate_planned_read_file_result(call, planned_read_file_keys)
            if duplicate_planned_read is not None:
                tool_results[call.id] = duplicate_planned_read
                continue
            repeat_result = self._repeat_tool_call_result(call, task, planned_fingerprints)
            if repeat_result is not None:
                tool_results[call.id] = repeat_result
                continue

            tool_call_count += 1

            if call.name in self.registry:
                read_key = _read_file_key(call)
                if read_key is not None:
                    planned_read_file_keys.add(read_key)
                    _record_read_fragment(call, planned_read_fragment_counts)
                fingerprint = _tool_call_fingerprint(call)
                if fingerprint is not None:
                    planned_fingerprints[fingerprint] = call.id
                known_tool_calls.append(self._with_runtime_tool_args(call, session))
                continue
            tool_results[call.id] = ToolResult(
                tool_name=call.name,
                ok=False,
                summary=f"unknown tool: {call.name}",
                error_code="UNKNOWN_TOOL",
            )
            self._record_tool_rejection(task, call, tool_results[call.id])
        return known_tool_calls, tool_results

    def _tool_budget_error(
        self,
        call,
        task: TaskState,
        tool_call_count: int,
    ) -> ToolResult | None:
        if tool_call_count >= task.limits.max_tool_calls_per_iteration:
            return ToolResult(
                tool_name=call.name,
                ok=False,
                summary="tool call rejected by iteration budget",
                error_code="TOOL_CALL_BUDGET_EXCEEDED",
                data={"limit": task.limits.max_tool_calls_per_iteration},
        )
        return None

    def _code_change_precondition_error(self, call, task: TaskState, session: SessionState) -> ToolResult | None:
        paths: Sequence[str | None]
        if call.name in CODE_WRITE_TOOLS:
            paths = [_tool_write_path(call)]
        elif call.name in SHELL_CODE_WRITE_TOOLS:
            paths = _shell_write_paths(call)
        else:
            return None
        path = next((item for item in paths if item is not None and _is_production_code_path(item)), None)
        if path is None or not _is_production_code_path(path):
            return None
        if _has_prior_test_execution(task, session):
            return None
        return ToolResult(
            tool_name=call.name,
            ok=False,
            summary="code change rejected: run a test case before editing production code",
            error_code="CODE_CHANGE_REQUIRES_TEST_FIRST",
            data={
                "path": path,
                "reason": "write or update a test case, execute it, then modify production code",
            },
        )

    def _report_write_precondition_error(self, call, task: TaskState) -> ToolResult | None:
        if call.name in CODE_WRITE_TOOLS:
            path = _tool_write_path(call) or ""
        elif call.name in SHELL_CODE_WRITE_TOOLS:
            paths = _shell_write_paths(call)
            if not paths:
                return None
            path = paths[0]
        else:
            return None
        if not _looks_like_report_goal(task.goal):
            return None
        if _goal_explicitly_requests_file_output(task.goal):
            return None
        return ToolResult(
            tool_name=call.name,
            ok=False,
            summary=f"report write rejected: answer inline instead of writing {path or 'file'}",
            error_code="REPORT_WRITE_REQUIRES_EXPLICIT_REQUEST",
            data={
                "path": path,
                "reason": "report-style requests should default to answering in chat unless the user explicitly asks for a file",
            },
        )

    def _fragmented_read_file_result(
        self,
        call,
        planned_read_fragment_counts: dict[tuple[str, str], int],
    ) -> ToolResult | None:
        fragment_key = _read_file_fragment_key(call)
        if fragment_key is None:
            return None
        if planned_read_fragment_counts.get(fragment_key, 0) < 2:
            return None
        path, _ = fragment_key
        return ToolResult(
            tool_name="read_file",
            ok=False,
            summary=f"read_file rejected: too many small fragments for {path}; use a larger max_bytes or targeted grep/sed command",
            error_code="READ_FILE_FRAGMENT_LIMIT_EXCEEDED",
            data={
                "path": path,
                "limit": 2,
                "reason": "avoid repeated fragmented reads of the same file in one iteration",
            },
        )

    def _duplicate_successful_read_file_result(self, call, task: TaskState) -> ToolResult | None:
        read_key = _read_file_key(call)
        if read_key is None:
            return None
        path, encoding, start_index, max_bytes = read_key
        for event in reversed(task.trace_events):
            if event.phase != "tool":
                continue
            if event.data.get("tool_name") != "read_file" or event.data.get("ok") is not True:
                continue
            args = event.data.get("args")
            if not isinstance(args, dict):
                continue
            previous_path = _normalize_relative_path(str(args.get("path", "")))
            previous_encoding = str(args.get("encoding", "utf-8"))
            previous_start_index = _optional_int_arg(args, "start_index")
            previous_max_bytes = _optional_int_arg(args, "max_bytes")
            if (
                previous_path != path
                or previous_encoding != encoding
                or previous_start_index != start_index
                or previous_max_bytes != max_bytes
            ):
                continue
            observation = self._observation_by_tool_call_id(task, str(event.data.get("tool_call_id", "")))
            return ToolResult(
                tool_name="read_file",
                ok=True,
                summary=f"read_file skipped: already read {path}",
                content=observation.content if observation is not None else "",
                data={
                    "deduplicated": True,
                    "path": path,
                    "source_tool_call_id": event.data.get("tool_call_id"),
                },
            )
        return None

    def _duplicate_planned_read_file_result(
        self,
        call,
        planned_read_file_keys: set[tuple[str, str, int | None, int | None]],
    ) -> ToolResult | None:
        read_key = _read_file_key(call)
        if read_key is None or read_key not in planned_read_file_keys:
            return None
        path, _, _, _ = read_key
        return ToolResult(
            tool_name="read_file",
            ok=True,
            summary=f"read_file skipped: duplicate request in current iteration {path}",
            data={
                "deduplicated": True,
                "path": path,
                "scope": "current_iteration",
            },
        )

    def _repeat_tool_call_result(
        self,
        call,
        task: TaskState,
        planned_fingerprints: dict[str, str],
    ) -> ToolResult | None:
        if _is_test_tool_call(call):
            return None
        fingerprint = _tool_call_fingerprint(call)
        if fingerprint is None:
            return None
        planned_call_id = planned_fingerprints.get(fingerprint)
        if planned_call_id is not None:
            return _duplicate_tool_result(
                call,
                fingerprint=fingerprint,
                source_tool_call_id=planned_call_id,
                scope="current_iteration",
            )
        for event in reversed(task.trace_events):
            if event.phase != "tool":
                continue
            if event.data.get("fingerprint") != fingerprint or event.data.get("ok") is not True:
                continue
            return _duplicate_tool_result(
                call,
                fingerprint=fingerprint,
                source_tool_call_id=str(event.data.get("tool_call_id") or ""),
                scope="previous_iteration",
                task=task,
            )
        return None

    def _observation_by_tool_call_id(self, task: TaskState, tool_call_id: str):
        if not tool_call_id:
            return None
        for observation in reversed(task.observations):
            if observation.tool_call_id == tool_call_id and observation.ok:
                return observation
        return None

    def _is_overview_goal(self, goal: str) -> bool:
        if any(keyword in goal for keyword in ["修改", "实现", "修复", "新增", "删除", "写入", "创建文件"]):
            return False
        return "项目" in goal and any(keyword in goal for keyword in OVERVIEW_GOAL_KEYWORDS)

    def _record_tool_rejection(self, task: TaskState, call, tool_result: ToolResult) -> None:
        task.trace_events.append(
            TraceEvent(
                phase="tool",
                message="Tool call rejected",
                data={
                    "tool_call_id": call.id,
                    "tool_name": call.name,
                    "summary": tool_result.summary,
                    "error_code": tool_result.error_code,
                },
            )
        )

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

    def _finalize_answer_content(self, task: TaskState, content: str) -> str:
        if _has_only_failed_webpage_fetches_after_search(task):
            if "页面内容读取失败" in content:
                return content
            disclaimer = "注意：本次联网搜索虽返回了搜索结果，但页面内容读取失败，下面内容不能视为已完成网页来源核实的结论。\n\n"
            return disclaimer + content
        if not _has_no_effective_web_search_results(task):
            return content
        if "未获取到有效搜索结果" in content:
            return content
        disclaimer = "注意：本次联网搜索未获取到有效搜索结果，下面内容基于已有知识整理，不能视为带来源核实的结论。\n\n"
        return disclaimer + content


def _normalize_relative_path(path: str) -> str:
    normalized = PurePosixPath(path.replace("\\", "/")).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _looks_like_report_goal(goal: str) -> bool:
    normalized = goal.lower()
    return any(keyword in normalized for keyword in REPORT_GOAL_KEYWORDS)


def _goal_explicitly_requests_file_output(goal: str) -> bool:
    normalized = goal.lower()
    return any(keyword in normalized for keyword in EXPLICIT_WRITE_INTENT_KEYWORDS)


def _has_no_effective_web_search_results(task: TaskState) -> bool:
    saw_search = False
    for event in task.trace_events:
        if event.phase != "tool":
            continue
        data = event.data
        if data.get("tool_name") != "web_search":
            continue
        saw_search = True
        summary = str(data.get("summary") or "")
        if data.get("ok") is True and not summary.startswith("No results found for:"):
            return False
    return saw_search


def _has_only_failed_webpage_fetches_after_search(task: TaskState) -> bool:
    saw_effective_search = False
    saw_fetch = False
    saw_successful_fetch = False
    for event in task.trace_events:
        if event.phase != "tool":
            continue
        data = event.data
        tool_name = data.get("tool_name")
        if tool_name == "web_search":
            summary = str(data.get("summary") or "")
            if data.get("ok") is True and not summary.startswith("No results found for:"):
                saw_effective_search = True
        elif tool_name == "fetch_webpage":
            saw_fetch = True
            if data.get("ok") is True:
                saw_successful_fetch = True
    return saw_effective_search and saw_fetch and not saw_successful_fetch


def _compression_target_label(strategies: list[str]) -> str:
    if "force_truncate" in strategies:
        return "较早的上下文消息、工具观察和低相关历史"
    if "history_summary" in strategies:
        return "较早的上下文消息和工具观察"
    return "过长工具输出"


def _read_file_key(call) -> tuple[str, str, int | None, int | None] | None:
    if call.name != "read_file":
        return None
    path = _normalize_relative_path(str(call.args.get("path", "")))
    if not path:
        return None
    encoding = str(call.args.get("encoding", "utf-8"))
    start_index = _optional_int_arg(call.args, "start_index")
    max_bytes = _optional_int_arg(call.args, "max_bytes")
    return path, encoding, start_index, max_bytes


def _read_file_fragment_key(call) -> tuple[str, str] | None:
    read_key = _read_file_key(call)
    if read_key is None:
        return None
    path, encoding, start_index, max_bytes = read_key
    if start_index is None and max_bytes is None:
        return None
    return path, encoding


def _record_read_fragment(call, planned_read_fragment_counts: dict[tuple[str, str], int]) -> None:
    fragment_key = _read_file_fragment_key(call)
    if fragment_key is None:
        return
    planned_read_fragment_counts[fragment_key] = planned_read_fragment_counts.get(fragment_key, 0) + 1


def _tool_call_fingerprint(call) -> str | None:
    try:
        encoded_args = json.dumps(
            _canonical_tool_args(call.name, sanitize_tool_args(call.args)),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return None
    return f"{call.name}:{encoded_args}"


def _canonical_tool_args(tool_name: str, args: dict) -> dict:
    canonical: dict = {}
    for key, value in args.items():
        if key in RUNTIME_ARG_KEYS:
            continue
        if key == "path" and isinstance(value, str):
            canonical[key] = _normalize_relative_path(value)
            continue
        canonical[key] = value
    if tool_name == "read_file":
        canonical.setdefault("encoding", "utf-8")
        for key in ("start_index", "max_bytes"):
            if key in canonical:
                canonical[key] = _optional_int_value(canonical[key])
    if tool_name == "list_files":
        canonical.setdefault("path", ".")
        canonical["path"] = _normalize_relative_path(str(canonical["path"]))
        if "limit" in canonical:
            canonical["limit"] = _optional_int_value(canonical["limit"])
    return canonical


def _duplicate_tool_result(
    call,
    fingerprint: str,
    source_tool_call_id: str,
    scope: str,
    task: TaskState | None = None,
) -> ToolResult | None:
    if call.name in REPEAT_CACHEABLE_TOOLS:
        observation = _find_successful_observation(task, source_tool_call_id)
        return ToolResult(
            tool_name=call.name,
            ok=True,
            summary=f"{call.name} skipped: duplicate request already completed",
            content=observation.content if observation is not None else "",
            data={
                "deduplicated": True,
                "fingerprint": fingerprint,
                "source_tool_call_id": source_tool_call_id,
                "scope": scope,
            },
        )
    if call.name in REPEAT_BLOCKED_TOOLS:
        return ToolResult(
            tool_name=call.name,
            ok=False,
            summary=f"{call.name} blocked: duplicate tool call already completed",
            error_code="DUPLICATE_TOOL_CALL_BLOCKED",
            data={
                "blocked_duplicate": True,
                "fingerprint": fingerprint,
                "source_tool_call_id": source_tool_call_id,
                "scope": scope,
            },
        )
    return None


def _find_successful_observation(task: TaskState | None, tool_call_id: str):
    if task is None or not tool_call_id:
        return None
    for observation in reversed(task.observations):
        if observation.tool_call_id == tool_call_id and observation.ok:
            return observation
    return None


def _has_prior_test_execution(task: TaskState, session: SessionState) -> bool:
    if any(event.phase == "tool" and _is_test_tool_event(event.data) for event in task.trace_events):
        return True
    test_tool_call_ids: set[str] = set()
    executed_tool_call_ids: set[str] = set()
    for message in session.messages:
        if message.role == "agent":
            names = message.metadata.get("tool_call_names")
            arguments = message.metadata.get("tool_call_arguments")
            if not isinstance(names, dict):
                continue
            if not isinstance(arguments, dict):
                arguments = {}
            for tool_call_id, tool_name in names.items():
                if tool_name not in {"run_bash", "run_temp_script"}:
                    continue
                raw_arguments = str(arguments.get(tool_call_id, "")).lower()
                if "test" in str(tool_call_id).lower() or "test" in raw_arguments:
                    test_tool_call_ids.add(str(tool_call_id))
        elif message.role == "tool" and message.tool_call_id:
            executed_tool_call_ids.add(message.tool_call_id)
    return bool(test_tool_call_ids & executed_tool_call_ids)


def _tool_write_path(call) -> str | None:
    raw_path = call.args.get("path")
    if raw_path is None:
        return None
    return _normalize_relative_path(str(raw_path))


def _shell_write_paths(call) -> list[str]:
    if call.name == "run_bash":
        text = str(call.args.get("command") or "")
    elif call.name == "run_temp_script":
        text = str(call.args.get("content") or "")
    else:
        return []
    patterns = [
        r"\bsed\s+-i(?:\s+['\"][^'\"]*['\"])?\s+['\"][^'\"]*['\"]\s+([A-Za-z0-9_.\-/]+)",
        r"\bperl\s+-pi(?:\s+['\"][^'\"]*['\"])?\s+['\"][^'\"]*['\"]\s+([A-Za-z0-9_.\-/]+)",
        r"\btee(?:\s+-a)?\s+(?!/)([A-Za-z0-9_.\-/]+)",
        r"(?:>>|>)\s*(?!&|\d|/)([A-Za-z0-9_.\-/]+)",
        r"\bPath\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\.write_text\s*\(",
        r"\bPath\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\.write_bytes\s*\(",
        r"\bPath\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\.open\s*\(\s*['\"][wa]",
        r"\bopen\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][wa]",
        r"\bPath\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\.touch\s*\(",
    ]
    paths: list[str] = []
    for pattern in patterns:
        paths.extend(_normalize_relative_path(match.group(1)) for match in re.finditer(pattern, text))
    paths.extend(_shell_touch_paths(text))
    return paths


def _shell_touch_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"(?:^|[;&|]\s*)touch\s+([^;&|\n]+)", text):
        try:
            tokens = shlex.split(match.group(1))
        except ValueError:
            continue
        paths.extend(_touch_command_paths(tokens))
    return paths


def _touch_command_paths(tokens: list[str]) -> list[str]:
    paths: list[str] = []
    options_done = False
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if not options_done and token == "--":
            options_done = True
            continue
        if not options_done and token.startswith("-") and token != "-":
            if token in {"-A", "-d", "-r", "-t"}:
                skip_next = True
            continue
        if token.startswith("/"):
            continue
        paths.append(_normalize_relative_path(token))
    return paths


def _is_production_code_path(path: str) -> bool:
    normalized = _normalize_relative_path(path)
    if _is_test_path(normalized):
        return False
    return PurePosixPath(normalized).suffix.lower() in CODE_FILE_SUFFIXES


def _is_test_path(path: str) -> bool:
    normalized = _normalize_relative_path(path).lower()
    parts = PurePosixPath(normalized).parts
    name = PurePosixPath(normalized).name
    return (
        "tests" in parts
        or "test" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".test.js")
        or name.endswith(".test.jsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.tsx")
        or name.endswith(".spec.js")
        or name.endswith(".spec.jsx")
    )


def _is_test_tool_event(data: dict) -> bool:
    if data.get("tool_name") not in {"run_bash", "run_temp_script"}:
        return False
    if data.get("is_test") is True:
        return True
    args = data.get("args")
    if not isinstance(args, dict):
        args = {}
    text = " ".join(
        str(value).lower()
        for value in [
            *args.values(),
            data.get("summary", ""),
            data.get("stdout", ""),
            data.get("stderr", ""),
        ]
    )
    return any(keyword in text for keyword in ["pytest", "unittest", "test", "go test", "cargo test", "npm test", "pnpm test", "yarn test", "ruff"])


def _is_test_tool_call(call) -> bool:
    return _is_test_tool_event({"tool_name": call.name, "args": call.args})


def _tool_event_failed(data: dict) -> bool:
    if data.get("ok") is False:
        return True
    return bool(data.get("error_code")) and data.get("ok") is not True


def _test_tool_event_passed(data: dict) -> bool:
    return data.get("ok") is True and data.get("exit_code") == 0 and not _test_output_has_failure(data)


def _test_output_has_failure(data: dict) -> bool:
    text = "\n".join(str(data.get(key) or "") for key in ["summary", "stdout", "stderr"]).lower()
    failure_markers = [
        " failed",
        "failed ",
        "failures",
        "error collecting",
        "traceback",
        "assertionerror",
        "command exited 1",
        "command exited 2",
        "exit status 1",
        "exit status 2",
    ]
    return any(marker in text for marker in failure_markers)


def _optional_int_arg(args: dict, key: str) -> int | None:
    if key not in args:
        return None
    return _optional_int_value(args[key])


def _optional_int_value(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _goal_mentions_current_project(goal: str) -> bool:
    normalized = _compact_cjk_spaces(goal.lower())
    return any(
        keyword in normalized
        for keyword in ["当前项目", "这个项目", "项目是做什么", "项目作用", "这个工程", "当前工程"]
    )


def _compact_cjk_spaces(text: str) -> str:
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
