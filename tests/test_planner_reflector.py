from pathlib import Path

from manus_mini.llm import LLMResult
from manus_mini.models import AgentError, LoopLimits, PlanStep, SessionState, TaskState, TraceEvent
from manus_mini.planner import Planner, build_planner_system_prompt
from manus_mini.reflection import ReflectionLoop
from manus_mini.reflector import Reflector


def test_planner_produces_steps_for_project_analysis(tmp_path: Path) -> None:
    class RecordingLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(
                content=(
                    "1. 扫描工作目录并识别项目结构 | research\n"
                    "2. 读取 README、pyproject 和技术文档 | research\n"
                    "3. 整理可执行结论并输出 Markdown 草稿 | report"
                )
            )

    planner = Planner(llm=RecordingLLM())
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


def test_planner_classifies_identity_questions_as_chat(tmp_path: Path) -> None:
    planner = Planner()
    session = SessionState.create(cwd=tmp_path)

    steps = planner.build_plan("你的名字是啥", session=session)

    assert len(steps) == 1
    assert steps[0].intent == "chat"


def test_planner_treats_cli_usage_errors_as_report_tasks(tmp_path: Path) -> None:
    planner = Planner()
    session = SessionState.create(cwd=tmp_path)

    steps = planner.build_plan("manus-mini list remove session-45dc2367524b 这个报错什么意思", session=session)

    assert steps
    assert all(step.intent != "chat" for step in steps)
    assert any("正确用法" in step.description for step in steps)


def test_planner_uses_llm_plan_when_available(tmp_path: Path) -> None:
    class RecordingLLM:
        def __init__(self) -> None:
            self.calls = 0
            self.messages = []

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.calls += 1
            self.messages.append(messages)
            return LLMResult(
                content=(
                    "1. 扫描工作目录并识别项目结构 | research\n"
                    "2. 读取 README 和设计文档 | research\n"
                    "3. 输出 Markdown 草稿 | report"
                )
            )

    planner = Planner(llm=RecordingLLM())
    session = SessionState.create(cwd=tmp_path)

    steps = planner.build_plan("请分析当前项目结构，并说明它的作用", session=session)

    assert planner.llm.calls == 1
    assert [step.description for step in steps] == [
        "扫描工作目录并识别项目结构",
        "读取 README 和设计文档",
        "输出 Markdown 草稿",
    ]
    assert [step.intent for step in steps] == ["research", "research", "report"]


def test_planner_corrects_chat_intent_for_file_reading_steps(tmp_path: Path) -> None:
    class BadIntentLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(content="1. 读取 README.md 了解项目概述 | chat")

    planner = Planner(llm=BadIntentLLM())
    session = SessionState.create(cwd=tmp_path)

    steps = planner.build_plan("这个项目是做什么的，简单的说，越简单越好", session=session)

    assert len(steps) == 1
    assert steps[0].description == "读取 README.md 了解项目概述"
    assert steps[0].intent == "research"


def test_planner_system_prompt_includes_identity_project_overview_and_tool_constraints(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "manus_mini").mkdir(parents=True)
    (tmp_path / "src" / "manus_mini" / "runtime.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "design.md").write_text("design", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_runtime.py").write_text("def test_x(): pass", encoding="utf-8")

    session = SessionState.create(cwd=tmp_path)

    prompt = build_planner_system_prompt(session)

    assert "你叫 manus-mini" in prompt
    assert "个人助理" in prompt
    assert "代码项目的查看、总结、诊断和优化建议" in prompt
    assert "修改、删除和生成" in prompt
    assert "文档写作" in prompt
    assert "深度行业研究报告" in prompt
    assert "工具使用要克制" in prompt
    assert "计划最多 4 步" in prompt
    assert "每一步必须带可验证产出" in prompt
    assert "避免重复 list_files/read_file" in prompt
    assert "当前项目基本信息" in prompt
    assert f"项目名：{tmp_path.name}" in prompt
    assert "项目代码目录结构" in prompt
    assert "src/：核心实现代码" in prompt
    assert "docs/：设计文档、问题记录和优化说明" in prompt


def test_planner_sends_identity_and_project_overview_to_llm(tmp_path: Path) -> None:
    class RecordingLLM:
        def __init__(self) -> None:
            self.messages = []

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            self.messages.append(messages)
            return LLMResult(content="1. 基于项目结构判断关键文件 | research")

    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
    llm = RecordingLLM()
    planner = Planner(llm=llm)
    session = SessionState.create(cwd=tmp_path)

    planner.build_plan("请分析当前项目结构", session=session)

    system_prompt = llm.messages[0][0].content
    assert llm.messages[0][0].role == "system"
    assert "你叫 manus-mini" in system_prompt
    assert "项目代码目录结构" in system_prompt
    assert "工具使用要克制" in system_prompt
    assert "先定位目标模块，再决定是否读取文件" in system_prompt
    assert "src/：核心实现代码" in system_prompt


def test_planner_falls_back_to_rules_when_llm_plan_is_empty(tmp_path: Path) -> None:
    class EmptyLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(content="")

    planner = Planner(llm=EmptyLLM())
    session = SessionState.create(cwd=tmp_path)

    steps = planner.build_plan("请分析当前项目结构，并说明它的作用", session=session)

    assert any("扫描工作目录" in step.description for step in steps)
    assert any(step.intent == "research" for step in steps)


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


def test_reflector_accepts_complete_risk_discussion(tmp_path: Path) -> None:
    reflector = Reflector()
    task = TaskState.create(goal="给出优化建议", cwd=tmp_path, limits=LoopLimits())

    draft = (
        "以下是按 P0-P3 划分的优化建议：\n"
        "P0：补齐测试和异常处理。\n"
        "P1：收敛上下文压缩策略。\n"
        "P2：优化 TUI 呈现。\n"
        "P3：补充文档。\n"
        "风险：如果外部模型不可用，流程会退回规则草稿，但结果仍然可读。"
    )

    decision = reflector.decide(task, draft)

    assert decision.decision == "accept"


def test_reflector_rejects_project_answer_that_asks_user_for_project_details(tmp_path: Path) -> None:
    reflector = Reflector()
    task = TaskState.create(goal="这个项目是做什么的，简单的说，越简单越好", cwd=tmp_path)

    draft = "您还没有告诉我具体是哪个项目呢？请先提供项目的描述、链接或代码。"

    decision = reflector.decide(task, draft)

    assert decision.decision == "local_update"
    assert decision.reason == "draft ignored current workspace project context"


def test_reflection_follow_up_context_includes_project_structure(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')", encoding="utf-8")
    task = TaskState.create(
        goal="这个项目是做什么的，简单的说",
        cwd=tmp_path,
        limits=LoopLimits(max_reflection_rounds=1),
    )
    loop = ReflectionLoop()

    context = loop._build_follow_up_context(
        task,
        "您还没有告诉我具体是哪个项目呢？请先提供项目的描述、链接或代码。",
        "draft ignored current workspace project context",
    )

    assert "项目代码目录结构" in context
    assert "README.md" in context
    assert "src/：核心实现代码" in context
    assert "不要要求用户再提供项目描述、链接或代码" in context


def test_reflection_loop_uses_llm_to_decide_draft_quality(tmp_path: Path) -> None:
    class FakeReactLoop:
        def run(self, task, session):  # noqa: ANN001, ANN201, ARG002
            return "您还没有告诉我具体是哪个项目呢？请先提供项目的描述、链接或代码。"

    class ReflectionLLM:
        def __init__(self) -> None:
            self.messages = []
            self.tool_names = []

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201
            self.messages.append(messages)
            self.tool_names.append(tool_names)
            return LLMResult(
                content='{"decision":"local_update","reason":"回答忽略了当前工作目录项目上下文"}'
            )

    llm = ReflectionLLM()
    task = TaskState.create(
        goal="这个项目是做什么的，简单的说",
        cwd=tmp_path,
        limits=LoopLimits(max_reflection_rounds=1),
    )
    session = SessionState.create(cwd=tmp_path)
    loop = ReflectionLoop(react_loop=FakeReactLoop(), llm=llm)

    result = loop.run(task, session)

    assert llm.messages
    assert llm.tool_names == [[]]
    assert result.decision == "local_update"
    assert result.reason == "回答忽略了当前工作目录项目上下文"
    assert "项目代码目录结构" in llm.messages[0][0].content
    assert "草稿只给泛泛建议、没有落到用户目标或项目事实时，不能 accept" in llm.messages[0][0].content
    assert "reason 需要指出下一步应补什么" in llm.messages[0][0].content


def test_reflection_rejects_code_change_without_test_run(tmp_path: Path) -> None:
    class AcceptingLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(content='{"decision":"accept","reason":"looks good"}')

    task = TaskState.create(goal="修改代码修复 bug", cwd=tmp_path)
    task.plan = [PlanStep(description="修改实现", intent="code")]
    session = SessionState.create(cwd=tmp_path)

    decision = ReflectionLoop(llm=AcceptingLLM())._decide(task, session, "已修改代码")

    assert decision.decision == "local_update"
    assert "测试" in decision.reason


def test_reflection_rejects_actual_code_write_without_test_even_when_goal_is_ui_worded(tmp_path: Path) -> None:
    class AcceptingLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(content='{"decision":"accept","reason":"looks good"}')

    task = TaskState.create(goal="优化 TUI 状态栏展示", cwd=tmp_path)
    task.plan = [PlanStep(description="调整 TUI 展示", intent="code")]
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool replace_in_file finished: ok",
            data={"tool_name": "replace_in_file", "ok": True, "summary": "replaced src/manus_mini/prompt_tui.py"},
        )
    )
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool run_bash finished: ok",
            data={
                "tool_name": "run_bash",
                "ok": True,
                "summary": "command exited 0",
                "exit_code": 0,
                "args": {"command": "python -m py_compile src/manus_mini/prompt_tui.py"},
            },
        )
    )
    session = SessionState.create(cwd=tmp_path)

    decision = ReflectionLoop(llm=AcceptingLLM())._decide(task, session, "已修改代码")

    assert decision.decision == "local_update"
    assert "测试" in decision.reason


def test_reflection_rejects_code_change_when_latest_test_failed(tmp_path: Path) -> None:
    class AcceptingLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(content='{"decision":"accept","reason":"looks good"}')

    task = TaskState.create(goal="修改代码修复 bug", cwd=tmp_path)
    task.plan = [PlanStep(description="修改实现", intent="code")]
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool run_temp_script finished: failed",
            data={
                "tool_name": "run_temp_script",
                "ok": False,
                "summary": "command exited 2",
                "error_code": "COMMAND_FAILED",
                "exit_code": 2,
                "stderr": "AssertionError: expected fixed behavior",
            },
        )
    )
    session = SessionState.create(cwd=tmp_path)

    decision = ReflectionLoop(llm=AcceptingLLM())._decide(task, session, "已修改代码")

    assert decision.decision == "regenerate"
    assert "AssertionError" in decision.reason


def test_reflection_accepts_code_change_when_latest_test_passed(tmp_path: Path) -> None:
    class AcceptingLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(content='{"decision":"accept","reason":"tests passed"}')

    task = TaskState.create(goal="修改代码修复 bug", cwd=tmp_path)
    task.plan = [PlanStep(description="修改实现", intent="code")]
    task.trace_events.append(
        TraceEvent(
            phase="tool",
            message="Tool run_temp_script finished: ok",
            data={
                "tool_name": "run_temp_script",
                "ok": True,
                "summary": "command exited 0",
                "exit_code": 0,
                "stdout": "3 passed",
            },
        )
    )
    session = SessionState.create(cwd=tmp_path)

    decision = ReflectionLoop(llm=AcceptingLLM())._decide(task, session, "已修改代码")

    assert decision.decision == "accept"


def test_reflection_rejects_code_change_when_any_test_after_latest_write_failed(tmp_path: Path) -> None:
    class AcceptingLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(content='{"decision":"accept","reason":"tests passed"}')

    task = TaskState.create(goal="修改代码修复 bug", cwd=tmp_path)
    task.plan = [PlanStep(description="修改实现", intent="code")]
    task.trace_events.extend(
        [
            TraceEvent(
                phase="tool",
                message="Tool replace_in_file finished: ok",
                data={"tool_name": "replace_in_file", "ok": True, "summary": "replaced app.py"},
            ),
            TraceEvent(
                phase="tool",
                message="Tool run_temp_script finished: failed",
                data={
                    "tool_name": "run_temp_script",
                    "ok": False,
                    "summary": "command exited 1",
                    "exit_code": 1,
                    "stderr": "FAILED test_old_case",
                    "is_test": True,
                },
            ),
            TraceEvent(
                phase="tool",
                message="Tool run_bash finished: ok",
                data={
                    "tool_name": "run_bash",
                    "ok": True,
                    "summary": "command exited 0",
                    "exit_code": 0,
                    "stdout": "ruff passed",
                    "args": {"command": "ruff check src tests"},
                },
            ),
        ]
    )
    session = SessionState.create(cwd=tmp_path)

    decision = ReflectionLoop(llm=AcceptingLLM())._decide(task, session, "已修改代码")

    assert decision.decision == "regenerate"
    assert "FAILED test_old_case" in decision.reason


def test_reflection_ignores_failed_tests_before_latest_write_when_new_tests_pass(tmp_path: Path) -> None:
    class AcceptingLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            return LLMResult(content='{"decision":"accept","reason":"tests passed"}')

    task = TaskState.create(goal="修改代码修复 bug", cwd=tmp_path)
    task.plan = [PlanStep(description="修改实现", intent="code")]
    task.trace_events.extend(
        [
            TraceEvent(
                phase="tool",
                message="Tool run_temp_script finished: failed",
                data={
                    "tool_name": "run_temp_script",
                    "ok": False,
                    "summary": "command exited 1",
                    "exit_code": 1,
                    "stderr": "FAILED before fix",
                    "is_test": True,
                },
            ),
            TraceEvent(
                phase="tool",
                message="Tool replace_in_file finished: ok",
                data={"tool_name": "replace_in_file", "ok": True, "summary": "replaced app.py"},
            ),
            TraceEvent(
                phase="tool",
                message="Tool run_temp_script finished: ok",
                data={
                    "tool_name": "run_temp_script",
                    "ok": True,
                    "summary": "command exited 0",
                    "exit_code": 0,
                    "stdout": "1 passed",
                    "is_test": True,
                },
            ),
        ]
    )
    session = SessionState.create(cwd=tmp_path)

    decision = ReflectionLoop(llm=AcceptingLLM())._decide(task, session, "已修改代码")

    assert decision.decision == "accept"
