from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from vora.skills.loader import load_skills_from_roots
from vora.skills.models import SkillSpec


BUILTIN_SKILLS = [
    SkillSpec(
        name="project-analysis",
        description="分析本地项目架构、工程质量、测试体系、安全边界和可改进点。",
        triggers=["项目分析", "架构", "工程质量", "测试体系", "安全边界", "可改进点"],
        instructions=(
            "面向当前工作目录做项目分析。先定位 README、pyproject、src、tests、docs 等关键入口；"
            "只读取能支撑结论的关键文件。输出时按架构、Agent 能力、工程质量、测试保障、风险和下一步分类。"
        ),
        tool_allowlist=["list_files", "read_file", "run_bash"],
        acceptance=["引用具体文件或模块", "说明设计取舍", "指出边界和风险"],
    ),
    SkillSpec(
        name="code-change",
        description="对本地代码做小步、可验证、带测试的修改。",
        triggers=["修复", "修改代码", "新增功能", "删除代码", "重构", "测试"],
        instructions=(
            "修改代码前先读取目标文件和相关测试。优先局部替换，避免无关重构；修改后运行最小相关测试，"
            "若失败则根据错误继续修复。最终说明修改点和验证命令。"
        ),
        tool_allowlist=[
            "list_files",
            "read_file",
            "write_file",
            "replace_in_file",
            "append_file",
            "make_directory",
            "run_bash",
            "run_temp_script",
        ],
        acceptance=["修改范围清晰", "测试或验证已执行", "说明剩余风险"],
    ),
    SkillSpec(
        name="production-code-demo",
        description="将当前项目作为生产级代码开发 Agent 进行展示，突出工程化能力、可扩展架构与未来演进规划。",
        triggers=["生产级", "生产化", "产品演示", "产品定位", "未来规划", "roadmap", "路线图", "发展方向"],
        instructions=(
            "当前产品定位是能够达到生产级别水平的代码开发 Agent，具备工程化代码生成、修改、测试与调试能力。"
            "未来还将扩展行业研究调查报告 Agent 功能，以及多 Agent 通信协作等能力。"
            "演示时重点展示：\n"
            "1）生产级代码开发能力 —— 代码生成、质量门禁（Reflection pytest gate）、安全边界（路径限制/文件读写直执行审计/命令风险判断）、"
            "工具调度（只读并行/写入串行）、上下文压缩等工程化特性；\n"
            "2）可扩展的 Agent 架构 —— Planner/ReAct/Reflection 协作链路、Skills 能力包机制、ToolRegistry 工具注册体系，"
            "展示架构如何支撑未来功能扩展；\n"
            "3）未来演进规划 —— 行研 Agent（行业研究/报告自动生成）、多 Agent 通信（Agent 间协作与消息传递），"
            "以及当前已在 roadmap 上的生产化方向（容器沙箱、向量记忆、流式输出、多 provider 容灾等）。\n"
            "注意不要把项目说成已全部完成，要诚实说明当前已实现的能力边界和明确的后续迭代方向。"
        ),
        tool_allowlist=["list_files", "read_file", "run_bash"],
        acceptance=["体现生产级工程能力", "说明架构可扩展性", "包含清晰的未来规划"],
    ),
]


class SkillRegistry:
    def __init__(self, skills: Iterable[SkillSpec] | None = None) -> None:
        self._skills: dict[str, SkillSpec] = {}
        for skill in skills or []:
            self.register(skill)

    @classmethod
    def default(cls, cwd: Path) -> "SkillRegistry":
        roots = [cwd / "skills", Path.home() / ".vora" / "skills"]
        return cls([*BUILTIN_SKILLS, *load_skills_from_roots(roots)])

    @classmethod
    def from_roots(cls, roots: Iterable[Path], include_builtin: bool = False) -> "SkillRegistry":
        skills = [*BUILTIN_SKILLS] if include_builtin else []
        skills.extend(load_skills_from_roots(roots))
        return cls(skills)

    def register(self, skill: SkillSpec) -> None:
        self._skills[skill.name] = skill

    def all(self) -> list[SkillSpec]:
        return list(self._skills.values())

    def match(self, goal: str) -> SkillSpec | None:
        normalized_goal = goal.lower()
        best_skill: SkillSpec | None = None
        best_score = 0
        for skill in self._skills.values():
            score = _score_skill(skill, normalized_goal)
            if score > best_score:
                best_skill = skill
                best_score = score
        return best_skill


def _score_skill(skill: SkillSpec, normalized_goal: str) -> int:
    score = 0
    for trigger in skill.triggers:
        normalized_trigger = trigger.lower()
        if normalized_trigger and normalized_trigger in normalized_goal:
            score += max(1, len(normalized_trigger))
    return score
