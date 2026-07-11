from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from pydantic import BaseModel

from vora.context import build_project_code_overview, should_include_project_code_overview
from vora.llm import LLMClient, LLMResult, extract_usage, openai_messages
from vora.logging import EventLogger
from vora.models import Message, SessionState, TaskState, TraceEvent
from vora.react import ReActLoop
from vora.reflector import ReflectionDecision, Reflector
from vora.token_breakdown import record_llm_token_breakdown
from vora.validation import looks_like_inline_test_script, looks_like_validation_command


class ReflectionResult(BaseModel):
    accepted: bool
    content: str
    reason: str
    decision: str = "accept"


def _record_session_usage(session: SessionState, payload: dict) -> None:
    usage = extract_usage(payload)
    if usage is None:
        return
    session.record_token_usage(
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )


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
        draft = self.react_loop.run(task, session)
        if not _is_code_modification_task(task):
            decision = "accept"
            reason = "non-code task accepted without pytest reflection gate"
            task.trace_events.append(
                TraceEvent(
                    phase="reflection",
                    message=f"Reflection decided {decision}: {reason}",
                    data={
                        "decision": decision,
                        "reason": reason,
                        "draft_preview": draft[:500],
                    },
                )
            )
            self._record_reflection_result(task, draft, decision, reason)
            return ReflectionResult(accepted=True, content=draft, reason=reason, decision=decision)

        pytest_gate = _run_pytest_reflection_gate(task, draft)
        task.trace_events.append(
            TraceEvent(
                phase="reflection",
                message=(
                    "Reflection pytest gate passed"
                    if pytest_gate["ok"]
                    else "Reflection pytest gate failed"
                ),
                data=pytest_gate,
            )
        )
        if not pytest_gate["ok"]:
            decision = "local_update"
            reason = _format_pytest_gate_failure_reason(task, pytest_gate)
            self._record_reflection_result(task, draft, decision, reason)
            return ReflectionResult(accepted=False, content=draft, reason=reason, decision=decision)

        reflection_decision = self._decide(task, session, draft)
        decision = reflection_decision.decision
        reason = reflection_decision.reason
        task.trace_events.append(
            TraceEvent(
                phase="reflection",
                message=f"Reflection decided {decision}: {reason}",
                data={
                    "decision": decision,
                    "reason": reason,
                    "draft_preview": draft[:500],
                },
            )
        )
        self._record_reflection_result(task, draft, decision, reason)
        return ReflectionResult(accepted=decision == "accept", content=draft, reason=reason, decision=decision)

    def _decide(self, task: TaskState, session: SessionState, draft: str) -> ReflectionDecision:
        llm = self._resolve_llm()
        if llm is None:
            return apply_code_test_gate(task, self.reflector.decide(task, draft), draft=draft)
        try:
            result = self._complete_reflection_llm(llm, task, session, draft)
            return apply_code_test_gate(task, _parse_reflection_decision(result.content), draft=draft)
        except Exception as error:
            task.trace_events.append(
                TraceEvent(
                    phase="reflection",
                    message="Reflection LLM failed, falling back to rules",
                    data={"error": str(error) or error.__class__.__name__},
                )
            )
            return apply_code_test_gate(task, self.reflector.decide(task, draft), draft=draft)

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
            record_llm_token_breakdown(
                self.logger,
                task.session_id or session.session_id or "unknown-session",
                task.run_id,
                stage="reflection",
                iteration=0,
                messages=messages,
                tool_names=[],
            )
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
        _record_session_usage(session, result.source_response)
        return result

    def _build_reflection_messages(self, task: TaskState, session: SessionState, draft: str) -> list[Message]:
        lines = [
            "你是 vora 的反思审查器，负责判断上一轮草稿是否真正满足用户任务。",
            "必须只返回 JSON，不要返回 Markdown，不要解释 JSON 之外的内容。",
            'JSON 结构：{"decision":"accept|local_update|regenerate|replan","reason":"简短原因"}',
            "reason 必须使用中文；如果模型产生 reasoning_content，也必须使用中文，不要输出英文思考句。",
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
            "- 草稿只给泛泛建议、没有落到用户目标或项目事实时，不能 accept。",
            "- 如果任务涉及代码修改、修复、生成或删除，必须确认测试脚本或测试命令已经运行且全部通过；未运行测试或测试失败都不能 accept。",
            "- reason 需要指出下一步应补什么，例如需要读取哪个范围、修正哪个结论、运行哪个验证。",
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
CODE_CHANGE_TOOLS = {"write_file", "replace_in_file", "append_file", "make_directory"}
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
REFLECTION_PYTEST_TIMEOUT_SECONDS = 30


def _run_pytest_reflection_gate(task: TaskState, draft: str) -> dict:
    case_content = _build_reflection_pytest_case(task, draft)
    with tempfile.TemporaryDirectory(prefix="vora-reflection-") as directory:
        case_path = Path(directory) / "test_reflection_acceptance.py"
        case_path.write_text(case_content, encoding="utf-8")
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", case_path.as_posix(), "-q"],
                cwd=task.cwd,
                text=True,
                capture_output=True,
                timeout=REFLECTION_PYTEST_TIMEOUT_SECONDS,
                check=False,
            )
            return {
                "ok": completed.returncode == 0,
                "case_path": case_path.as_posix(),
                "case_content": case_content,
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        except subprocess.TimeoutExpired as error:
            return {
                "ok": False,
                "case_path": case_path.as_posix(),
                "case_content": case_content,
                "exit_code": None,
                "stdout": error.stdout or "",
                "stderr": error.stderr or f"pytest timed out after {REFLECTION_PYTEST_TIMEOUT_SECONDS}s",
            }


def _build_reflection_pytest_case(task: TaskState, draft: str) -> str:
    payload = {
        "original_input": task.goal,
        "draft": draft,
        "has_passing_test_after_latest_code_change": _has_passing_test_after_latest_code_change(task),
        "test_failures": [
            _format_test_failure(event)
            for event in _test_events_after_latest_code_change(task)
            if not _test_event_passed(event)
        ],
    }
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return textwrap.dedent(
        f"""
        import json


        PAYLOAD = json.loads({payload_json!r})


        def test_reflection_draft_is_not_empty():
            assert PAYLOAD["draft"].strip(), "reflection draft is empty"


        def test_code_task_has_passing_validation_after_latest_change():
            assert PAYLOAD["has_passing_test_after_latest_code_change"], (
                "代码类任务需要在最新代码变更后有通过的测试证据；"
                f"original_input={{PAYLOAD['original_input']!r}}; "
                f"test_failures={{PAYLOAD['test_failures']!r}}"
            )
        """
    ).lstrip()


def _has_passing_test_after_latest_code_change(task: TaskState) -> bool:
    test_events = _test_events_after_latest_code_change(task)
    return bool(test_events) and all(_test_event_passed(event) for event in test_events)


def _format_pytest_gate_failure_reason(task: TaskState, result: dict) -> str:
    stdout = str(result.get("stdout") or "").strip()
    stderr = str(result.get("stderr") or "").strip()
    output = "\n".join(part for part in [stdout, stderr] if part).strip()
    if len(output) > 1200:
        output = output[:1200] + "\n... [truncated]"
    case_content = str(result.get("case_content") or "")
    if len(case_content) > 1200:
        case_content = case_content[:1200] + "\n... [truncated]"
    return "\n".join(
        [
            "Reflection pytest gate failed; executor must continue from the failed acceptance case.",
            f"原始输入：{task.goal}",
            f"pytest_case_path：{result.get('case_path')}",
            "pytest_case：",
            case_content,
            "pytest_output：",
            output or "[empty]",
        ]
    )


def apply_code_test_gate(task: TaskState, decision: ReflectionDecision, draft: str = "") -> ReflectionDecision:
    delegated_choice_decision = apply_delegated_choice_gate(task, decision, draft=draft)
    if delegated_choice_decision is not decision:
        return delegated_choice_decision
    if decision.decision != "accept" or not _is_code_modification_task(task):
        return decision
    test_events = _test_events_after_latest_code_change(task)
    if not test_events:
        return ReflectionDecision("local_update", "代码修改任务尚未执行测试脚本或测试命令，需先补充并运行测试。")
    failed_test = next((event for event in test_events if not _test_event_passed(event)), None)
    if failed_test is None:
        return decision
    return ReflectionDecision("regenerate", f"测试未通过：{_format_test_failure(failed_test)}")


def apply_delegated_choice_gate(task: TaskState, decision: ReflectionDecision, draft: str = "") -> ReflectionDecision:
    if decision.decision not in {"accept", "local_update"}:
        return decision
    combined = f"{decision.reason}\n{draft}\n{task.result}".lower()
    if _goal_delegates_choice(task.goal) and _text_waits_for_user_choice(combined):
        return ReflectionDecision("regenerate", "用户已授权自行选择方案；不要等待用户选择，请直接选用保守文案完成修改并验证。")
    return decision


def _is_code_modification_task(task: TaskState) -> bool:
    if _latest_code_change_event_index(task) >= 0:
        return True
    normalized = task.goal.lower()
    has_change = any(keyword in normalized for keyword in CODE_CHANGE_KEYWORDS)
    has_code_target = any(keyword in normalized for keyword in CODE_TARGET_KEYWORDS)
    if any(step.intent == "code" for step in task.plan) and has_code_target:
        return True
    return has_change and has_code_target


def _goal_delegates_choice(goal: str) -> bool:
    normalized = goal.lower()
    return any(
        phrase in normalized
        for phrase in [
            "没想好",
            "你来定",
            "你决定",
            "你帮我选",
            "你看着办",
            "随便",
            "反正",
            "whatever",
            "you decide",
        ]
    )


def _text_waits_for_user_choice(text: str) -> bool:
    return any(
        phrase in text
        for phrase in [
            "等待用户",
            "用户选择",
            "用户确认",
            "让用户选择",
            "你看看哪个",
            "选好后",
            "等你选",
            "等你确认",
            "wait for user",
            "choose one",
        ]
    )


def _test_events_after_latest_code_change(task: TaskState) -> list[dict]:
    latest_code_change_index = _latest_code_change_event_index(task)
    events: list[dict] = []
    for index, event in enumerate(task.trace_events):
        if index <= latest_code_change_index or event.phase != "tool":
            continue
        data = event.data
        if _is_test_event(data):
            events.append(data)
    return events


def _latest_code_change_event_index(task: TaskState) -> int:
    for index in range(len(task.trace_events) - 1, -1, -1):
        event = task.trace_events[index]
        if event.phase != "tool":
            continue
        data = event.data
        if data.get("tool_name") in CODE_CHANGE_TOOLS and data.get("ok") is True:
            return index
    return -1


def _is_test_event(data: dict) -> bool:
    tool_name = data.get("tool_name")
    if tool_name not in COMMAND_TEST_TOOLS:
        return False
    if data.get("is_test") is True:
        return True
    if tool_name == "run_temp_script" and "args" not in data:
        return True
    return _looks_like_test_command(data)


def _test_event_passed(data: dict) -> bool:
    return data.get("ok") is True and data.get("exit_code") == 0 and not _test_output_has_failure(data)


def _looks_like_test_command(data: dict) -> bool:
    tool_name = data.get("tool_name")
    args = data.get("args")
    if not isinstance(args, dict):
        args = {}
    command = str(args.get("command") or args.get("script") or args.get("content") or args.get("filename") or "")
    if looks_like_validation_command(command):
        return True
    if tool_name == "run_temp_script" and looks_like_inline_test_script(command):
        return True
    return bool(data.get("validation") is True)


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
