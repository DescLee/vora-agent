from __future__ import annotations

import json

from pydantic import BaseModel

from manus_mini.context import build_project_code_overview, should_include_project_code_overview
from manus_mini.llm import LLMClient, LLMResult, openai_messages
from manus_mini.logging import EventLogger
from manus_mini.models import Message, SessionState, TaskState, TraceEvent
from manus_mini.react import ReActLoop
from manus_mini.reflector import ReflectionDecision, Reflector


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
        llm: LLMClient | None = None,
        logger: EventLogger | None = None,
    ) -> None:
        self.react_loop = react_loop or ReActLoop()
        self.reflector = reflector or Reflector()
        self.llm = llm
        self.logger = logger

    def run(self, task: TaskState, session: SessionState) -> ReflectionResult:
        rounds = max(1, task.limits.max_reflection_rounds)
        best_content = ""
        last_decision = "replan"
        last_reason = "no reflection output"

        for _ in range(rounds):
            draft = self.react_loop.run(task, session)
            best_content = draft
            reflection = self._decide(task, session, draft)
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

    def _decide(self, task: TaskState, session: SessionState, draft: str) -> ReflectionDecision:
        llm = self._resolve_llm()
        if llm is None:
            return self.reflector.decide(task, draft)
        try:
            result = self._complete_reflection_llm(llm, task, session, draft)
            return _parse_reflection_decision(result.content)
        except Exception as error:
            task.trace_events.append(
                TraceEvent(
                    phase="reflection",
                    message="Reflection LLM failed, falling back to rules",
                    data={"error": str(error) or error.__class__.__name__},
                )
            )
            return self.reflector.decide(task, draft)

    def _resolve_llm(self) -> LLMClient | None:
        if self.llm is not None:
            return self.llm
        react_llm = getattr(self.react_loop, "llm", None)
        if react_llm is not None:
            self.llm = react_llm
            return self.llm
        react_resolver = getattr(self.react_loop, "_resolve_llm", None)
        if callable(react_resolver):
            self.llm = react_resolver()
            return self.llm
        return None

    def _complete_reflection_llm(
        self,
        llm: LLMClient,
        task: TaskState,
        session: SessionState,
        draft: str,
    ) -> LLMResult:
        messages = self._build_reflection_messages(task, session, draft)
        request_payload = {"messages": openai_messages(messages), "tool_names": []}
        if self.logger is not None:
            self.logger.record(
                task.session_id or session.session_id or "unknown-session",
                task.run_id,
                {
                    "type": "llm_request",
                    "stage": "reflection",
                    "request": request_payload,
                    "api_request_payload": request_payload,
                },
            )
        result = llm.complete_with_tools(messages, [])
        api_request_payload = result.source_request or request_payload
        api_response_raw = result.source_response or result.model_dump(mode="json")
        if self.logger is not None:
            self.logger.record(
                task.session_id or session.session_id or "unknown-session",
                task.run_id,
                {
                    "type": "llm_response",
                    "stage": "reflection",
                    "request": api_request_payload,
                    "response": api_response_raw,
                    "api_request_payload": api_request_payload,
                    "api_response_raw": api_response_raw,
                },
            )
        return result

    def _build_reflection_messages(self, task: TaskState, session: SessionState, draft: str) -> list[Message]:
        lines = [
            "你是 manus-mini 的反思审查器，负责判断上一轮草稿是否真正满足用户任务。",
            "必须只返回 JSON，不要返回 Markdown，不要解释 JSON 之外的内容。",
            'JSON 结构：{"decision":"accept|local_update|regenerate|replan","reason":"简短原因"}',
            "",
            "决策含义：",
            "- accept：草稿已经满足用户目标，可以作为最终答案。",
            "- local_update：草稿方向基本正确，但需要基于现有上下文局部修订。",
            "- regenerate：草稿不可用，需要重新生成答案。",
            "- replan：计划方向错误或缺少关键步骤，需要重新规划。",
            "",
            "判断要求：",
            "- 如果用户问当前项目/这个项目/这个工程，默认指当前工作目录。",
            "- 如果草稿要求用户提供当前项目描述、链接或代码，而当前上下文已经有工作目录信息，应判为 local_update。",
            "- 项目或代码相关问题需要结合项目目录结构、工具观察和草稿内容判断。",
            "- 不要调用工具。",
            "",
            f"用户任务：{task.goal}",
            f"当前工作目录：{task.cwd}",
            "",
            "当前执行计划：",
        ]
        if task.plan:
            lines.extend(f"- [{step.status}] {step.description} | {step.intent}" for step in task.plan)
        else:
            lines.append("- [empty]")
        recent_observations = [
            observation.summary
            for observation in task.observations[-8:]
            if observation.summary.strip()
        ]
        lines.extend(["", "最近工具观察："])
        if recent_observations:
            lines.extend(f"- {summary}" for summary in recent_observations)
        else:
            lines.append("- [empty]")
        if should_include_project_code_overview(task.goal):
            lines.extend(["", build_project_code_overview(task.cwd)])
        lines.extend(["", "待审查草稿：", draft])
        return [Message.system("\n".join(lines))]

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
        if should_include_project_code_overview(task.goal):
            lines.extend(
                [
                    "",
                    "当前项目上下文：",
                    build_project_code_overview(task.cwd),
                    "",
                    "纠偏要求：用户提到“当前项目/这个项目/这个工程”时，指的就是当前工作目录。不要要求用户再提供项目描述、链接或代码；请基于目录结构决定是否继续调用工具。",
                ]
            )
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
                "draft": draft,
                "reflection_context": _build_loggable_reflection_context(task),
            },
        )


def _short_text(content: str, limit: int = 160) -> str:
    compact = " ".join(content.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _build_loggable_reflection_context(task: TaskState) -> dict:
    return {
        "user_goal": task.goal,
        "cwd": str(task.cwd),
        "status": task.status,
        "step_count": task.step_count,
        "plan": [
            {
                "description": step.description,
                "intent": step.intent,
                "status": step.status,
            }
            for step in task.plan
        ],
        "observations": [
            {
                "tool_call_id": observation.tool_call_id,
                "ok": observation.ok,
                "summary": observation.summary,
                "content": observation.content,
            }
            for observation in task.observations
        ],
        "errors": [
            {
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
            }
            for error in task.errors
        ],
    }


def _parse_reflection_decision(content: str) -> ReflectionDecision:
    payload = _parse_json_object(content)
    decision = str(payload.get("decision", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    if decision not in {"accept", "local_update", "regenerate", "replan"}:
        raise ValueError(f"invalid reflection decision: {decision or '[empty]'}")
    return ReflectionDecision(decision=decision, reason=reason or "LLM reflection decision")


def _parse_json_object(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("reflection response must be a JSON object")
    return payload
