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
        name="interview-demo",
        description="把当前项目作为面试作品讲解，突出 Agent 工程能力、系统边界和可验证成果。",
        triggers=["面试", "8年经验", "八年经验", "演示", "项目亮点", "怎么讲"],
        instructions=(
            "面试场景下不要把项目包装成完整生产平台。重点讲清楚 Agent Runtime 的目标、Planner/ReAct/"
            "Reflection/ToolScheduler/Session/Memory/Logging 的协作链路，以及为什么这些设计能体现工程经验。"
        ),
        tool_allowlist=["list_files", "read_file", "run_bash"],
        acceptance=["突出工程取舍", "能映射到代码模块", "包含不足和后续规划"],
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
