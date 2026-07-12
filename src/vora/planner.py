from __future__ import annotations

import re
from typing import cast

from vora.context import build_cached_project_code_overview
from vora.llm import LLMClient, LLMRequestError, extract_usage, get_default_llm_client, openai_messages
from vora.logging import EventLogger
from vora.models import Message, PlanIntent, PlanStep, SessionState
from vora.skills import SkillSpec
from vora.token_breakdown import record_llm_token_breakdown


PLAN_INSTRUCTIONS = (
    "你叫 vora，是用户的个人助理。"
    "你专门负责代码项目的查看、总结、诊断和优化建议；"
    "也具备代码能力，可以对项目代码进行查看、总结、修改、删除和生成。"
    "除此以外，你还可以进行文档写作、文档总结，以及深度行业研究报告撰写。"
    "\n\n"
    "你现在处于 Planner 阶段，职责是根据用户目标、最近对话和当前项目结构，制定简洁可执行的计划。"
    "所有对用户可见或会进入 trace/TUI 的内容都必须使用中文，包括 reasoning_content、计划描述、原因说明和最终文本；"
    "不要输出英文思考句。"
    "涉及项目或代码时，先基于下方目录结构判断需要查看的范围；只有信息不足时才计划调用工具。"
    "先定位目标模块，再决定是否读取文件；不要把“查看项目”默认扩成全仓库扫描。"
    "工具使用要克制：优先复用已有上下文，优先少量关键文件，避免重复 list_files/read_file，避免无目的地全量扫描。"
    "代码审查、问题清单、项目诊断类任务应先规划用只读 shell 命令批量定位线索，再按证据读取少量文件。"
    "如果用户表达“没想好、你来定、你看着办、反正换个”等授权你代为选择的意思，不要规划等待用户选择；"
    "应自行选择保守方案并继续执行。"
    "计划最多 4 步，每一步必须带可验证产出，例如定位范围、读取依据、修改点、测试或最终结论。"
    "\n\n"
    "输出格式：每行一条计划，格式为：`序号. 计划描述 | intent`。"
    "intent 只能是 chat、research、code_review、code_edit、code_validate、code、automation、report 之一。"
    "查看代码问题、代码审查、架构风险、列问题清单使用 code_review；修改/修复/生成代码使用 code_edit；运行测试/验证使用 code_validate。"
    "不要输出多余解释，不要输出 JSON。"
)


class Planner:
    def __init__(self, llm: LLMClient | None = None, logger: EventLogger | None = None) -> None:
        self.llm = llm
        self.logger = logger
        self.last_reasoning_content = ""

    def build_plan(
        self,
        goal: str,
        session: SessionState,
        run_id: str | None = None,
        active_skill: SkillSpec | None = None,
    ) -> list[PlanStep]:
        self.last_reasoning_content = ""
        normalized = goal.lower()
        if _looks_like_cli_issue(goal, normalized):
            return self._build_cli_issue_plan(goal)
        if _is_small_talk(goal, normalized):
            return [PlanStep(description="让 LLM 直接回复用户，不调用本地文件工具", intent="chat")]

        llm_steps, reasoning = self._build_llm_plan(goal, session, run_id=run_id, active_skill=active_skill)
        self.last_reasoning_content = reasoning
        if llm_steps:
            return _deduplicate_plan(_rewrite_delegated_choice_plan(goal, llm_steps))

        return _deduplicate_plan(_rewrite_delegated_choice_plan(goal, self._build_rule_plan(goal, normalized)))

    def _build_llm_plan(
        self,
        goal: str,
        session: SessionState,
        run_id: str | None = None,
        active_skill: SkillSpec | None = None,
    ) -> tuple[list[PlanStep], str]:
        messages = self._build_prompt_messages(goal, session, active_skill=active_skill)
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
            return [], ""
        self._record_llm_response(
            session,
            run_id,
            messages,
            tool_names,
            api_request_payload=result.source_request or {},
            api_response_raw=result.source_response or result.model_dump(mode="json"),
        )
        _record_session_usage(session, result.source_response)
        return self._parse_llm_plan(result.content), result.reasoning_content

    def _build_prompt_messages(
        self,
        goal: str,
        session: SessionState,
        active_skill: SkillSpec | None = None,
    ) -> list[Message]:
        recent_messages = session.messages[-8:]
        recent_lines = [f"- {message.role}: {message.content}" for message in recent_messages]
        context = "\n".join(recent_lines) if recent_lines else "- 无"
        return [
            Message.system(build_planner_system_prompt(session, active_skill=active_skill)),
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
        record_llm_token_breakdown(
            self.logger,
            session.session_id,
            run_id or "planner",
            stage="planner",
            iteration=0,
            messages=messages,
            tool_names=list(tool_names),
        )
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


def _record_session_usage(session: SessionState, payload: dict) -> None:
    usage = extract_usage(payload)
    if usage is None:
        return
    session.record_token_usage(
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        cached_prompt_tokens=usage.get("cached_prompt_tokens"),
        non_cached_prompt_tokens=usage.get("non_cached_prompt_tokens"),
    )


def build_planner_system_prompt(session: SessionState, active_skill: SkillSpec | None = None) -> str:
    workspace = session.cwd.expanduser().resolve()
    parts = [
        PLAN_INSTRUCTIONS,
        "",
        "当前项目基本信息",
        f"- 项目名：{workspace.name}",
        f"- 工作目录：{workspace}",
        "",
        build_cached_project_code_overview(workspace),
    ]
    if active_skill is not None:
        parts.extend(["", format_skill_for_planner(active_skill)])
    return "\n".join(parts)


def format_skill_for_planner(skill: SkillSpec) -> str:
    lines = [
        "当前命中的 Skill",
        f"- 名称：{skill.name}",
        f"- 说明：{skill.description or '无'}",
    ]
    if skill.instructions:
        lines.append(f"- 计划约束：{skill.instructions}")
    if skill.tool_allowlist:
        lines.append(f"- 建议工具范围：{', '.join(skill.tool_allowlist)}")
    if skill.acceptance:
        lines.append(f"- 验收标准：{'; '.join(skill.acceptance)}")
    return "\n".join(lines)


def _split_plan_line(line: str) -> tuple[str, str | None]:
    if "|" in line:
        description, intent = line.rsplit("|", 1)
        return description.strip(), intent.strip()
    if "：" in line:
        description, intent = line.rsplit("：", 1)
        if _is_known_intent(intent):
            return description.strip(), intent.strip()
    return line.strip(), None


def _normalize_intent(intent: str | None, description: str) -> PlanIntent:
    normalized_intent = intent.strip().lower() if intent and _is_known_intent(intent) else None
    description_lower = description.lower()
    if normalized_intent == "chat" and _description_requires_project_or_file_work(description_lower):
        normalized_intent = None
    if normalized_intent == "code" and _description_is_code_review(description_lower):
        return "code_review"
    if normalized_intent and normalized_intent != "chat":
        return cast(PlanIntent, normalized_intent)
    if _description_is_code_review(description_lower):
        return "code_review"
    if _description_is_code_validate(description_lower):
        return "code_validate"
    if _description_is_code_edit(description_lower):
        return "code_edit"
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
    return value.strip().lower() in {
        "chat",
        "research",
        "code",
        "code_review",
        "code_edit",
        "code_validate",
        "automation",
        "report",
    }


def _description_is_code_review(description_lower: str) -> bool:
    if _description_is_code_edit(description_lower) or _description_is_code_validate(description_lower):
        return False
    return any(keyword in description_lower for keyword in ["代码", "源码", "模块", "架构"]) and any(
        keyword in description_lower
        for keyword in ["审查", "分析", "查看", "看看", "问题", "风险", "清单", "质量", "评估"]
    )


def _description_is_code_edit(description_lower: str) -> bool:
    return any(keyword in description_lower for keyword in ["修改", "修复", "新增", "删除", "生成代码", "重构", "替换", "补丁"])


def _description_is_code_validate(description_lower: str) -> bool:
    return any(keyword in description_lower for keyword in ["测试", "验证", "lint", "pytest", "mypy", "ruff", "构建"])


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


def _rewrite_delegated_choice_plan(goal: str, steps: list[PlanStep]) -> list[PlanStep]:
    if not _goal_delegates_choice(goal):
        return steps
    rewritten: list[PlanStep] = []
    inserted_direct_change_step = False
    for step in steps:
        if _step_waits_for_user_choice(step.description):
            if not inserted_direct_change_step:
                rewritten.append(PlanStep(description="自行选择一组保守文案并直接修改对应文件", intent="code"))
                inserted_direct_change_step = True
            continue
        rewritten.append(step)
    return rewritten


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


def _step_waits_for_user_choice(description: str) -> bool:
    normalized = description.lower()
    return any(
        phrase in normalized
        for phrase in [
            "等待用户",
            "让用户选择",
            "用户选择",
            "用户确认",
            "等待确认",
            "选项",
            "choose",
            "wait for user",
        ]
    )


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
        "vora",
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
        keyword in combined for keyword in ["run", "remove", "list", "resume"]
    )
