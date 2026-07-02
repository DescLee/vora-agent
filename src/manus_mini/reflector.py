from __future__ import annotations

from dataclasses import dataclass

from manus_mini.context import should_include_project_code_overview
from manus_mini.models import TaskState


@dataclass(slots=True)
class ReflectionDecision:
    decision: str
    reason: str


class Reflector:
    def decide(self, task: TaskState, draft: str) -> ReflectionDecision:
        text = draft.strip()
        last_error = task.errors[-1] if task.errors else None
        goal_text = task.goal.lower()

        if not text:
            return ReflectionDecision("replan", "draft is empty")

        if _looks_like_cli_issue(goal_text) and not _cli_usage_explained(text):
            return ReflectionDecision("local_update", "cli usage answer is incomplete")

        if should_include_project_code_overview(task.goal) and _asks_user_for_current_project_details(text):
            return ReflectionDecision(
                "local_update",
                "draft ignored current workspace project context",
            )

        if last_error is not None and last_error.retryable:
            return ReflectionDecision("regenerate", f"retryable error: {last_error.code}")

        if any(keyword in text for keyword in ["待补充", "需要补充", "TODO", "不够", "有风险"]):
            return ReflectionDecision("local_update", "draft needs local refinement")

        if "重新规划" in text or ("重新生成" in text and len(text) < 20):
            return ReflectionDecision("replan", "draft asks for replanning")

        return ReflectionDecision("accept", "draft is sufficient")


def _looks_like_cli_issue(text: str) -> bool:
    return any(
        keyword in text
        for keyword in [
            "manus-mini",
            "usage:",
            "unrecognized arguments",
            "argparse",
            "命令行",
            "命令写法",
            "session_id",
        ]
    )


def _cli_usage_explained(text: str) -> bool:
    return any(keyword in text for keyword in ["正确用法", "remove", "list", "resume", "tui", "子命令"])


def _asks_user_for_current_project_details(text: str) -> bool:
    normalized = " ".join(text.strip().lower().split())
    if not normalized:
        return False
    return any(
        phrase in normalized
        for phrase in [
            "请先提供项目",
            "请提供项目",
            "提供项目的描述",
            "提供项目描述",
            "提供项目的链接",
            "提供项目链接",
            "提供项目的代码",
            "提供项目代码",
            "提供项目的描述、链接或代码",
            "项目的描述、链接或代码",
            "没有告诉我具体是哪个项目",
            "无法得知具体项目",
            "不知道具体项目",
            "which project",
            "provide the project",
            "share the project",
        ]
    )
