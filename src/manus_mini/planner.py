from __future__ import annotations

import re
from typing import Literal, cast

from manus_mini.llm import LLMClient, LLMRequestError, get_default_llm_client
from manus_mini.models import Message, PlanStep, SessionState


PLAN_INSTRUCTIONS = (
    "你是本地项目任务规划器。"
    "请根据用户目标和最近对话，输出一个简洁的执行计划。"
    "每行一条计划，格式为：`序号. 计划描述 | intent`。"
    "intent 只能是 chat、research、code、automation、report 之一。"
    "不要输出多余解释，不要输出 JSON。"
)


class Planner:
    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def build_plan(self, goal: str, session: SessionState) -> list[PlanStep]:
        normalized = goal.lower()
        if _looks_like_cli_issue(goal, normalized):
            return self._build_cli_issue_plan(goal)
        if _is_small_talk(goal, normalized):
            return [PlanStep(description="让 LLM 直接回复用户，不调用本地文件工具", intent="chat")]

        llm_plan = self._build_llm_plan(goal, session)
        if llm_plan:
            return _deduplicate_plan(llm_plan)

        return _deduplicate_plan(self._build_rule_plan(goal, normalized))

    def _build_llm_plan(self, goal: str, session: SessionState) -> list[PlanStep]:
        messages = self._build_prompt_messages(goal, session)
        try:
            result = self._resolve_llm().complete_with_tools(messages, [])
        except (LLMRequestError, ValueError, TypeError, KeyError, IndexError):
            return []
        return self._parse_llm_plan(result.content)

    def _build_prompt_messages(self, goal: str, session: SessionState) -> list[Message]:
        recent_messages = session.messages[-8:]
        recent_lines = [f"- {message.role}: {message.content}" for message in recent_messages]
        context = "\n".join(recent_lines) if recent_lines else "- 无"
        return [
            Message.system(PLAN_INSTRUCTIONS),
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
    if intent and _is_known_intent(intent):
        return cast(Literal["chat", "research", "code", "automation", "report"], intent.strip().lower())
    description_lower = description.lower()
    if any(keyword in description_lower for keyword in ["读取", "查看", "分析", "调研", "扫描", "research"]):
        return "research"
    if any(keyword in description_lower for keyword in ["写", "生成", "创建", "修改", "更新", "补充", "code"]):
        return "code"
    if any(keyword in description_lower for keyword in ["todo", "清单", "整理", "归类", "automation"]):
        return "automation"
    if any(keyword in description_lower for keyword in ["回复", "聊天", "问候", "chat"]):
        return "chat"
    return "report"


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
    small_talk_keywords = ["你好", "您好", "hello", "hi", "状态怎么样", "在吗"]
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
