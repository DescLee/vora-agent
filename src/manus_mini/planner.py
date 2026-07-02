from __future__ import annotations

import re
from typing import Literal, cast

from manus_mini.context import build_project_code_overview
from manus_mini.llm import LLMClient, LLMRequestError, get_default_llm_client, openai_messages
from manus_mini.logging import EventLogger
from manus_mini.models import Message, PlanStep, SessionState


PLAN_INSTRUCTIONS = (
    "你叫 manus-mini，是用户的个人助理。"
    "你专门负责代码项目的查看、总结、诊断和优化建议；"
    "也具备代码能力，可以对项目代码进行查看、总结、修改、删除和生成。"
    "除此以外，你还可以进行文档写作、文档总结，以及深度行业研究报告撰写。"
    "\n\n"
    "你现在处于 Planner 阶段，职责是根据用户目标、最近对话和当前项目结构，制定简洁可执行的计划。"
    "涉及项目或代码时，先基于下方目录结构判断需要查看的范围；只有信息不足时才计划调用工具。"
    "工具使用要克制：优先复用已有上下文，优先少量关键文件，避免重复 list_files/read_file，避免无目的地全量扫描。"
    "\n\n"
    "输出格式：每行一条计划，格式为：`序号. 计划描述 | intent`。"
    "intent 只能是 chat、research、code、automation、report 之一。"
    "不要输出多余解释，不要输出 JSON。"
)


class Planner:
    def __init__(self, llm: LLMClient | None = None, logger: EventLogger | None = None) -> None:
        self.llm = llm
        self.logger = logger

    def build_plan(self, goal: str, session: SessionState, run_id: str | None = None) -> list[PlanStep]:
        normalized = goal.lower()
        if _looks_like_cli_issue(goal, normalized):
            return self._build_cli_issue_plan(goal)
        if _is_small_talk(goal, normalized):
            return [PlanStep(description="让 LLM 直接回复用户，不调用本地文件工具", intent="chat")]

        llm_plan = self._build_llm_plan(goal, session, run_id=run_id)
        if llm_plan:
            return _deduplicate_plan(llm_plan)

        return _deduplicate_plan(self._build_rule_plan(goal, normalized))

    def _build_llm_plan(self, goal: str, session: SessionState, run_id: str | None = None) -> list[PlanStep]:
        messages = self._build_prompt_messages(goal, session)
        tool_names: list[str] = []
        self._record_llm_request(session, run_id, messages, tool_names)
        try:
            result = self._resolve_llm().complete_with_tools(messages, tool_names)
        except (LLMRequestError, ValueError, TypeError, KeyError, IndexError):
            self._record_llm_response(
                session,
                run_id,
                messages,
                tool_names,
                api_response_raw={"error": "planner llm request failed", "fallback": True},
            )
            return []
        self._record_llm_response(
            session,
            run_id,
            messages,
            tool_names,
            api_request_payload=result.source_request or {},
            api_response_raw=result.source_response or result.model_dump(mode="json"),
        )
        return self._parse_llm_plan(result.content)

    def _build_prompt_messages(self, goal: str, session: SessionState) -> list[Message]:
        recent_messages = session.messages[-8:]
        recent_lines = [f"- {message.role}: {message.content}" for message in recent_messages]
        context = "\n".join(recent_lines) if recent_lines else "- 无"
        return [
            Message.system(build_planner_system_prompt(session)),
            Message.user(
                "\n".join(
                    [
                        f"用户目标：{goal}",
                        "最近对话：",
                        context,
                        "请输出计划：",
                    ]
                )
            ),
        ]

    def _parse_llm_plan(self, content: str) -> list[PlanStep]:
        steps: list[PlanStep] = []
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^[\-\*\d\.\)\(、\s]+", "", line).strip()
            if not line:
                continue
            description, intent = _split_plan_line(line)
            if not description:
                continue
            steps.append(PlanStep(description=description, intent=_normalize_intent(intent, description)))
        return steps

    def _build_rule_plan(self, goal: str, normalized: str) -> list[PlanStep]:
        plan: list[PlanStep] = []

        if any(keyword in goal for keyword in ["项目", "project", "目录", "结构", "分析"]):
            plan.append(PlanStep(description="扫描工作目录并识别项目结构", intent="research"))
            plan.append(PlanStep(description="读取 README、pyproject 和技术文档", intent="research"))
            plan.append(PlanStep(description="整理可执行结论并输出 Markdown 草稿", intent="report"))

        if any(keyword in goal for keyword in ["写", "生成", "创建", "修改", "补充", "更新", "读取", "查看"]):
            plan.append(PlanStep(description="确认目标文件或产物位置", intent="code"))
            plan.append(PlanStep(description="生成可执行修改或产物内容", intent="code"))

        if any(keyword in normalized for keyword in ["todo", "清单", "整理", "归类"]):
            plan.append(PlanStep(description="抽取待办并按优先级整理", intent="automation"))

        if not plan:
            plan.append(PlanStep(description="分析用户目标并生成草稿结果", intent="report"))

        return plan

    def _build_cli_issue_plan(self, goal: str) -> list[PlanStep]:
        return [
            PlanStep(description="识别命令结构和报错信息", intent="report"),
            PlanStep(description="指出命令写法错误并给出正确用法", intent="report"),
            PlanStep(description="如果需要，补充可直接执行的示例", intent="report"),
        ]

    def _resolve_llm(self) -> LLMClient:
        if self.llm is None:
            self.llm = get_default_llm_client()
        return self.llm

    def _record_llm_request(
        self,
        session: SessionState,
        run_id: str | None,
        messages: list[Message],
        tool_names: list[str],
    ) -> None:
        if self.logger is None:
            return
        self.logger.record(
            session.session_id,
            run_id or "planner",
            {
                "type": "llm_request",
                "stage": "planner",
                "iteration": 0,
                "request": {"messages": openai_messages(messages), "tool_names": list(tool_names)},
                "api_request_payload": {"messages": openai_messages(messages), "tool_names": list(tool_names)},
            },
        )

    def _record_llm_response(
        self,
        session: SessionState,
        run_id: str | None,
        messages: list[Message],
        tool_names: list[str],
        api_request_payload: dict | None = None,
        api_response_raw: dict | None = None,
    ) -> None:
        if self.logger is None:
            return
        fallback_request = {"messages": openai_messages(messages), "tool_names": list(tool_names)}
        self.logger.record(
            session.session_id,
            run_id or "planner",
            {
                "type": "llm_response",
                "stage": "planner",
                "iteration": 0,
                "request": api_request_payload or fallback_request,
                "response": api_response_raw or {},
                "api_request_payload": api_request_payload or fallback_request,
                "api_response_raw": api_response_raw or {},
            },
        )


def build_planner_system_prompt(session: SessionState) -> str:
    workspace = session.cwd.expanduser().resolve()
    return "\n".join(
        [
            PLAN_INSTRUCTIONS,
            "",
            "当前项目基本信息",
            f"- 项目名：{workspace.name}",
            f"- 工作目录：{workspace}",
            "",
            build_project_code_overview(workspace),
        ]
    )


def _split_plan_line(line: str) -> tuple[str, str | None]:
    if "|" in line:
        description, intent = line.rsplit("|", 1)
        return description.strip(), intent.strip()
    if "：" in line:
        description, intent = line.rsplit("：", 1)
        if _is_known_intent(intent):
            return description.strip(), intent.strip()
    return line.strip(), None


def _normalize_intent(intent: str | None, description: str) -> Literal["chat", "research", "code", "automation", "report"]:
    normalized_intent = intent.strip().lower() if intent and _is_known_intent(intent) else None
    description_lower = description.lower()
    if normalized_intent == "chat" and _description_requires_project_or_file_work(description_lower):
        normalized_intent = None
    if normalized_intent and normalized_intent != "chat":
        return cast(Literal["chat", "research", "code", "automation", "report"], normalized_intent)
    if any(keyword in description_lower for keyword in ["读取", "查看", "分析", "调研", "扫描", "research"]):
        return "research"
    if any(keyword in description_lower for keyword in ["写", "生成", "创建", "修改", "更新", "补充", "code"]):
        return "code"
    if any(keyword in description_lower for keyword in ["todo", "清单", "整理", "归类", "automation"]):
        return "automation"
    if normalized_intent == "chat":
        return "chat"
    if any(keyword in description_lower for keyword in ["回复", "聊天", "问候", "chat"]):
        return "chat"
    return "report"


def _description_requires_project_or_file_work(description_lower: str) -> bool:
    return any(
        keyword in description_lower
        for keyword in [
            "读取",
            "查看",
            "分析",
            "调研",
            "扫描",
            "readme",
            "pyproject",
            ".md",
            ".py",
            "项目",
            "代码",
            "文件",
        ]
    )


def _is_known_intent(value: str) -> bool:
    return value.strip().lower() in {"chat", "research", "code", "automation", "report"}


def _deduplicate_plan(steps: list[PlanStep]) -> list[PlanStep]:
    seen: set[tuple[str, str]] = set()
    deduplicated: list[PlanStep] = []
    for step in steps:
        key = (step.intent, step.description)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(step)
    return deduplicated


def _is_small_talk(goal: str, normalized: str) -> bool:
    stripped = goal.strip()
    small_talk_keywords = [
        "你好",
        "您好",
        "hello",
        "hi",
        "状态怎么样",
        "在吗",
        "你的名字",
        "你叫什么",
        "你是谁",
        "介绍下自己",
        "介绍一下自己",
    ]
    file_or_task_keywords = [
        "读取",
        "查看",
        "文件",
        ".md",
        ".py",
        "项目",
        "目录",
        "结构",
        "写",
        "生成",
        "创建",
        "修改",
        "更新",
        "继续",
        "api_key",
        "password",
        "token",
    ]
    return any(keyword in normalized or keyword in stripped for keyword in small_talk_keywords) and not any(
        keyword in normalized or keyword in stripped for keyword in file_or_task_keywords
    )


def _looks_like_cli_issue(goal: str, normalized: str) -> bool:
    combined = f"{goal}\n{normalized}".lower()
    cli_keywords = [
        "manus-mini",
        "usage:",
        "unrecognized arguments",
        "error:",
        "argparse",
        "session_id",
        "子命令",
        "命令行",
        "命令写法",
    ]
    if any(keyword in combined for keyword in cli_keywords):
        return True
    return any(token in combined for token in ["`", "➜", "$ ", "git:(main)"]) and any(
        keyword in combined for keyword in ["remove", "list", "resume", "tui"]
    )
