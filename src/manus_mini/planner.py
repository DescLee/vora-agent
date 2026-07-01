from __future__ import annotations

from manus_mini.models import PlanStep, SessionState


class Planner:
    def build_plan(self, goal: str, session: SessionState) -> list[PlanStep]:  # noqa: ARG002
        normalized = goal.lower()
        plan: list[PlanStep] = []

        if any(keyword in goal for keyword in ["项目", "project", "目录", "结构", "分析"]):
            plan.append(PlanStep(description="扫描工作目录并识别项目结构", intent="research"))
            plan.append(PlanStep(description="读取 README、pyproject 和技术文档", intent="research"))
            plan.append(PlanStep(description="整理可执行结论并输出 Markdown 草稿", intent="report"))

        if any(keyword in goal for keyword in ["写", "生成", "创建", "修改", "补充", "更新"]):
            plan.append(PlanStep(description="确认目标文件或产物位置", intent="code"))
            plan.append(PlanStep(description="生成可执行修改或产物内容", intent="code"))

        if any(keyword in normalized for keyword in ["todo", "清单", "整理", "归类"]):
            plan.append(PlanStep(description="抽取待办并按优先级整理", intent="automation"))

        if not plan:
            plan.append(PlanStep(description="分析用户目标并生成草稿结果", intent="report"))

        return _deduplicate_plan(plan)


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
