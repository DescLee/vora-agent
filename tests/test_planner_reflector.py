from pathlib import Path

from manus_mini.models import AgentError, LoopLimits, SessionState, TaskState
from manus_mini.planner import Planner
from manus_mini.reflector import Reflector


def test_planner_produces_steps_for_project_analysis(tmp_path: Path) -> None:
    planner = Planner()
    session_goal = "请分析当前项目结构，并说明它的作用"
    session = SessionState.create(cwd=tmp_path)

    steps = planner.build_plan(session_goal, session=session)

    assert any("扫描工作目录" in step.description for step in steps)
    assert any(step.intent == "research" for step in steps)
    assert any(step.intent == "report" for step in steps)


def test_planner_classifies_small_talk_as_chat(tmp_path: Path) -> None:
    planner = Planner()
    session = SessionState.create(cwd=tmp_path)

    steps = planner.build_plan("你好，今天状态怎么样？", session=session)

    assert len(steps) == 1
    assert steps[0].intent == "chat"
    assert "直接回复" in steps[0].description


def test_reflector_covers_accept_update_regenerate_and_replan(tmp_path: Path) -> None:
    reflector = Reflector()
    task = TaskState.create(goal="写报告", cwd=tmp_path, limits=LoopLimits())

    accept = reflector.decide(task, "这是一个完整的报告草稿。")
    update = reflector.decide(task, "内容还需要补充技术风险，待补充。")
    retrying_task = TaskState.create(goal="写报告", cwd=tmp_path)
    retrying_task.errors.append(AgentError(code="TOOL_TIMEOUT", message="timeout", retryable=True))
    regenerate = reflector.decide(retrying_task, "上一次结果失败，需要重新生成。")
    replan = reflector.decide(task, "")

    assert accept.decision == "accept"
    assert update.decision == "local_update"
    assert regenerate.decision == "regenerate"
    assert replan.decision == "replan"
