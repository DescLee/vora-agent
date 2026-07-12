import json
from pathlib import Path

from vora.logging import EventLogger
from vora.llm import LLMResult
from vora.models import PlanStep, SessionState, TaskState
from vora.planner import Planner
from vora.prompt_tui_formatting import format_process
from vora.plugins.manager import install_plugin
from vora.react import ReActLoop
from vora.runtime import AgentRuntime
from vora.skills import SkillRegistry, SkillSpec
from vora.skills.loader import load_skill_from_dir
from vora.skills.manager import add_skill, skills_root


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


def test_load_skill_from_codex_style_skill_md(tmp_path: Path) -> None:
    skill_dir = tmp_path / "codex-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: understand-anything
description: Analyze a codebase and explain architecture
---

# Understand Anything

Read the repository structure first, then explain modules and relationships.
""",
        encoding="utf-8",
    )

    skill = load_skill_from_dir(skill_dir)

    assert skill is not None
    assert skill.name == "understand-anything"
    assert skill.description == "Analyze a codebase and explain architecture"
    assert "Read the repository structure first" in skill.instructions
    assert "understand-anything" in skill.triggers


def test_add_skill_clones_github_url_and_installs_skill_md(tmp_path: Path, monkeypatch) -> None:
    def fake_run(command, check, stdout, stderr, text):  # noqa: ANN001, ANN202, ARG001
        clone_target = Path(command[-1])
        clone_target.mkdir(parents=True)
        (clone_target / "SKILL.md").write_text(
            """---
name: superpowers
description: Superpowers workflows
---

Use systematic debugging and verification before completion.
""",
            encoding="utf-8",
        )
        return None

    monkeypatch.setattr("vora.skills.manager.subprocess.run", fake_run)

    skill, target = add_skill(tmp_path, "https://github.com/example/superpowers.git")

    assert skill.name == "superpowers"
    assert target == skills_root(tmp_path, global_scope=False) / "superpowers"
    assert (target / "SKILL.md").is_file()


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


def test_runtime_logs_and_traces_active_skill(tmp_path: Path) -> None:
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
    logger = EventLogger(tmp_path / "logs", enabled=True)
    runtime = AgentRuntime(llm=None, cwd=tmp_path, logger=logger)
    runtime.reflection_loop = AcceptingReflection()

    result = runtime.on_user_message("我要拿这个项目去面试", session)

    assert result.active_task is not None
    skill_events = [
        event
        for event in result.active_task.trace_events
        if event.data.get("message_type") == "skill_activated"
    ]
    assert skill_events
    assert skill_events[0].data["skill_name"] == "interview-demo"
    log_path = tmp_path / "logs" / result.session_id / "node.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    active_skill_rows = [row for row in rows if row.get("type") == "active_skill"]
    assert active_skill_rows
    assert active_skill_rows[0]["skill_name"] == "interview-demo"


def test_tui_process_shows_active_skill_notice(tmp_path: Path) -> None:
    session = SessionState.create(cwd=tmp_path)
    task = TaskState.create(goal="面试", cwd=tmp_path)
    task.metadata["active_skill"] = {
        "name": "interview-demo",
        "description": "面试演示",
        "instructions": "按面试演示流程回答。",
        "tool_allowlist": ["list_files"],
        "acceptance": [],
    }
    session.active_task = task

    process = format_process(session)

    assert "启用 Skill：interview-demo" in process
    assert "面试演示" in process


def test_skill_registry_loads_installed_plugin_skills(tmp_path: Path, monkeypatch) -> None:
    plugin_home = tmp_path / "home" / ".vora"
    monkeypatch.setattr("vora.plugins.manager.default_vora_home", lambda: plugin_home)

    def fake_run(command, check, stdout, stderr, text):  # noqa: ANN001, ANN202, ARG001
        clone_target = Path(command[-1])
        skill_dir = clone_target / "skills" / "debugging"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: systematic-debugging
description: Debug systematically
---

Find root cause before fixing.
""",
            encoding="utf-8",
        )
        return None

    monkeypatch.setattr("vora.plugins.manager.subprocess.run", fake_run)
    install_plugin("https://github.com/example/superpowers.git", name="superpowers")

    registry = SkillRegistry.default(tmp_path)
    skill = registry.match("请 systematic-debugging 一下这个问题")

    assert skill is not None
    assert skill.name == "systematic-debugging"
    assert "Find root cause" in skill.instructions
