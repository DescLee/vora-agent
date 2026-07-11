import json
from pathlib import Path

from vora.llm import LLMResult
from vora.models import PlanStep, SessionState, TaskState
from vora.planner import Planner
from vora.react import ReActLoop
from vora.runtime import AgentRuntime
from vora.skills import SkillRegistry, SkillSpec


def write_skill(root: Path, name: str, metadata: dict, instructions: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.json").write_text(json.dumps(metadata), encoding="utf-8")
    (skill_dir / "instructions.md").write_text(instructions, encoding="utf-8")
    return skill_dir


def test_skill_registry_loads_project_skill_and_matches_trigger(tmp_path: Path) -> None:
    write_skill(
        tmp_path / "skills",
        "project-analysis",
        {
            "name": "project-analysis",
            "description": "分析项目",
            "triggers": ["面试", "架构"],
            "tool_allowlist": ["list_files", "read_file"],
            "acceptance": ["引用具体文件"],
        },
        "先看 README，再看测试。",
    )

    registry = SkillRegistry.from_roots([tmp_path / "skills"])

    skill = registry.match("我准备拿这个项目去面试，帮我分析架构")

    assert skill is not None
    assert skill.name == "project-analysis"
    assert skill.instructions == "先看 README，再看测试。"
    assert skill.tool_allowlist == ["list_files", "read_file"]
    assert skill.acceptance == ["引用具体文件"]


def test_skill_registry_ignores_invalid_skill_directory(tmp_path: Path) -> None:
    invalid_dir = tmp_path / "skills" / "broken"
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "skill.json").write_text("{bad json", encoding="utf-8")

    registry = SkillRegistry.from_roots([tmp_path / "skills"])

    assert registry.all() == []
    assert registry.match("broken") is None


def test_planner_includes_active_skill_in_prompt(tmp_path: Path) -> None:
    class RecordingLLM:
        def __init__(self) -> None:
            self.system_prompt = ""

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.system_prompt = messages[0].content
            return LLMResult(content="1. 按 Skill 分析项目 | research")

    llm = RecordingLLM()
    planner = Planner(llm=llm)
    session = SessionState.create(cwd=tmp_path)
    skill = SkillSpec(
        name="project-analysis",
        description="面试项目分析",
        instructions="输出时必须按架构、质量、风险分类。",
        tool_allowlist=["list_files", "read_file"],
        acceptance=["引用具体文件"],
    )

    steps = planner.build_plan("分析项目", session=session, active_skill=skill)

    assert [(step.description, step.intent) for step in steps] == [("按 Skill 分析项目", "research")]
    assert "当前命中的 Skill" in llm.system_prompt
    assert "project-analysis" in llm.system_prompt
    assert "输出时必须按架构、质量、风险分类。" in llm.system_prompt
    assert "引用具体文件" in llm.system_prompt


def test_react_uses_active_skill_prompt_and_tool_allowlist(tmp_path: Path) -> None:
    class RecordingLLM:
        def __init__(self) -> None:
            self.system_prompt = ""
            self.tool_names: list[str] = []

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201
            self.system_prompt = messages[0].content
            self.tool_names = list(tool_names)
            return LLMResult(content="done")

    task = TaskState.create(goal="分析项目", cwd=tmp_path)
    task.plan = [PlanStep(description="按 Skill 分析项目", intent="research")]
    task.metadata["active_skill"] = {
        "name": "project-analysis",
        "description": "面试项目分析",
        "instructions": "必须说明工程取舍。",
        "tool_allowlist": ["list_files", "read_file", "missing_tool"],
        "acceptance": ["引用具体文件"],
    }
    session = SessionState.create(cwd=tmp_path)
    llm = RecordingLLM()

    result = ReActLoop(llm=llm).run(task, session)

    assert result == "done"
    assert llm.tool_names == ["list_files", "read_file"]
    assert "当前启用 Skill" in llm.system_prompt
    assert "project-analysis" in llm.system_prompt
    assert "必须说明工程取舍。" in llm.system_prompt


def test_runtime_records_matched_skill_on_task(tmp_path: Path) -> None:
    class AcceptingReflection:
        def run(self, task, session):  # noqa: ANN001, ANN201, ARG002
            task.result = "done"
            return type("Reflection", (), {"content": "done", "decision": "accept", "accepted": True, "reason": ""})()

    write_skill(
        tmp_path / "skills",
        "interview-demo",
        {
            "name": "interview-demo",
            "description": "面试演示",
            "triggers": ["面试"],
            "tool_allowlist": ["list_files", "read_file"],
        },
        "按面试演示流程回答。",
    )
    session = SessionState.create(cwd=tmp_path)
    runtime = AgentRuntime(llm=None, cwd=tmp_path)
    runtime.reflection_loop = AcceptingReflection()

    result = runtime.on_user_message("我要拿这个项目去面试", session)

    assert result.active_task is not None
    assert result.active_task.metadata["active_skill"]["name"] == "interview-demo"
    assert result.active_task.metadata["active_skill"]["instructions"] == "按面试演示流程回答。"
