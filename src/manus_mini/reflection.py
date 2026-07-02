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
            return apply_code_test_gate(task, self.reflector.decide(task, draft))
        try:
            result = self._complete_reflection_llm(llm, task, session, draft)
            return apply_code_test_gate(task, _parse_reflection_decision(result.content))
        except Exception as error:
            task.trace_events.append(
                TraceEvent(
                    phase="reflection",
                    message="Reflection LLM failed, falling back to rules",
                    data={"error": str(error) or error.__class__.__name__},
                )
            )
            return apply_code_test_gate(task, self.reflector.decide(task, draft))

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
            "- 如果任务涉及代码修改、修复、生成或删除，必须确认测试脚本或测试命令已经运行且全部通过；未运行测试或测试失败都不能 accept。",
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


COMMAND_TEST_TOOLS = {"run_bash", "run_temp_script"}
CODE_CHANGE_KEYWORDS = (
    "修改",
    "修复",
    "新增",
    "实现",
    "删除",
    "重构",
    "改造",
    "生成",
    "创建",
    "新建",
    "写入",
    "改成",
    "fix",
    "implement",
    "refactor",
)
CODE_TARGET_KEYWORDS = (
    "代码",
    "bug",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".go",
    ".java",
    ".rs",
    ".sh",
    "函数",
    "方法",
    "类",
    "接口",
    "测试",
)
TEST_COMMAND_KEYWORDS = (
    "pytest",
    "unittest",
    "test",
    "go test",
    "cargo test",
    "npm test",
    "pnpm test",
    "yarn test",
    "ruff",
)


def apply_code_test_gate(task: TaskState, decision: ReflectionDecision) -> ReflectionDecision:
    if decision.decision != "accept" or not _is_code_modification_task(task):
        return decision
    latest_test = _latest_test_event(task)
    if latest_test is None:
        return ReflectionDecision("local_update", "代码修改任务尚未执行测试脚本或测试命令，需先补充并运行测试。")
    if latest_test.get("ok") is True and latest_test.get("exit_code") == 0:
        return decision
    return ReflectionDecision("regenerate", f"测试未通过：{_format_test_failure(latest_test)}")


def _is_code_modification_task(task: TaskState) -> bool:
    normalized = task.goal.lower()
    has_change = any(keyword in normalized for keyword in CODE_CHANGE_KEYWORDS)
    has_code_target = any(keyword in normalized for keyword in CODE_TARGET_KEYWORDS)
    if any(step.intent == "code" for step in task.plan) and has_code_target:
        return True
    return has_change and has_code_target


def _latest_test_event(task: TaskState) -> dict | None:
    for event in reversed(task.trace_events):
        if event.phase != "tool":
            continue
        data = event.data
        tool_name = data.get("tool_name")
        if tool_name not in COMMAND_TEST_TOOLS:
            continue
        if tool_name == "run_temp_script" or _looks_like_test_command(data):
            return data
    return None


def _looks_like_test_command(data: dict) -> bool:
    args = data.get("args")
    if not isinstance(args, dict):
        return False
    text = " ".join(str(value).lower() for value in args.values())
    return any(keyword in text for keyword in TEST_COMMAND_KEYWORDS)


def _format_test_failure(data: dict) -> str:
    parts = []
    exit_code = data.get("exit_code")
    if exit_code is not None:
        parts.append(f"exit_code={exit_code}")
    stderr = str(data.get("stderr") or "").strip()
    stdout = str(data.get("stdout") or "").strip()
    summary = str(data.get("summary") or "").strip()
    if stderr:
        parts.append(stderr[:300])
    elif stdout:
        parts.append(stdout[:300])
    elif summary:
        parts.append(summary)
    return " | ".join(parts) or "测试失败"
