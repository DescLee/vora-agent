from pathlib import Path

from manus_mini.context import (
    ContextIntegrityError,
    build_project_code_overview,
    build_segments,
    compact_messages,
    compact_messages_with_snapshot,
    complete_interrupted_tool_messages,
    estimate_tokens,
    should_include_project_code_overview,
    validate_tool_call_pairs,
)
from manus_mini.llm import LLMResult
from manus_mini.models import Message


def test_estimate_tokens_uses_v1_rules() -> None:
    assert estimate_tokens("abcd", "mixed") == 2
    assert estimate_tokens("中文", "zh") == 2
    assert estimate_tokens("one two", "en") == 2
    assert estimate_tokens("print('hello')", "code") == 4


def test_validate_tool_call_pairs_accepts_complete_tool_exchange() -> None:
    messages = [
        Message.user("start"),
        Message.agent("need file", tool_call_ids=["call-1", "call-2"]),
        Message.tool("file content", tool_call_id="call-1"),
        Message.tool("more content", tool_call_id="call-2"),
        Message.user("next"),
    ]

    validate_tool_call_pairs(messages)


def test_validate_tool_call_pairs_rejects_orphan_tool_result() -> None:
    messages = [Message.tool("orphan", tool_call_id="call-1")]

    try:
        validate_tool_call_pairs(messages)
    except ContextIntegrityError as exc:
        assert "tool_call_id" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ContextIntegrityError")


def test_complete_interrupted_tool_messages_adds_cancelled_results_for_missing_tool_calls() -> None:
    messages = [
        Message.user("start"),
        Message.agent("need file", tool_call_ids=["call-1", "call-2"]),
        Message.tool("file content", tool_call_id="call-1"),
        Message.user("next"),
    ]

    inserted = complete_interrupted_tool_messages(messages)

    assert inserted == 1
    validate_tool_call_pairs(messages)
    assert messages[3].role == "tool"
    assert messages[3].tool_call_id == "call-2"
    assert "USER_CANCELLED" in messages[3].content
    assert messages[4].content == "next"


def test_complete_interrupted_tool_messages_converts_orphan_tool_results() -> None:
    messages = [Message.tool("orphan", tool_call_id="call-1")]

    inserted = complete_interrupted_tool_messages(messages)

    assert inserted == 0
    validate_tool_call_pairs(messages)
    assert messages[0].role == "system"
    assert "orphan tool result" in messages[0].content


def test_build_segments_keeps_tool_exchange_together() -> None:
    messages = [
        Message.user("start"),
        Message.agent("need file", tool_call_ids=["call-1"]),
        Message.tool("file content", tool_call_id="call-1"),
        Message.user("done"),
    ]

    segments = build_segments(messages)

    assert [segment.kind for segment in segments] == [
        "plain_message",
        "tool_exchange",
        "plain_message",
    ]
    assert [message.role for message in segments[1].messages] == ["agent", "tool"]
    assert segments[1].messages[0].tool_call_ids == ["call-1"]
    assert segments[1].messages[1].tool_call_id == "call-1"


def test_compact_messages_summarizes_old_messages_without_splitting_tool_exchange() -> None:
    messages = [
        Message.user("很早以前的需求：" + "A" * 80),
        Message.agent("need file", tool_call_ids=["call-1"]),
        Message.tool("file content " + "B" * 80, tool_call_id="call-1"),
        Message.user("最新需求：请基于上文继续"),
    ]

    compacted = compact_messages(messages, token_budget=30)

    validate_tool_call_pairs(compacted)
    assert compacted[0].role == "system"
    assert compacted[0].content.startswith("历史上下文摘要：")
    assert compacted[-1].content == "最新需求：请基于上文继续"
    assert not any(message.role == "tool" for message in compacted[1:])


def test_compact_messages_redacts_sensitive_content_in_summary() -> None:
    messages = [
        Message.user("旧需求包含 API_KEY=sk-live-secret " + "A" * 80),
        Message.agent("need file", tool_call_ids=["call-1"]),
        Message.tool("文件里有 password=abc123 " + "B" * 80, tool_call_id="call-1"),
        Message.user("最新需求"),
    ]

    compacted = compact_messages(messages, token_budget=20)

    assert compacted[0].role == "system"
    assert "sk-live-secret" not in compacted[0].content
    assert "password=abc123" not in compacted[0].content
    assert "[REDACTED]" in compacted[0].content


def test_compact_messages_can_use_llm_summary() -> None:
    class SummaryLLM:
        def __init__(self) -> None:
            self.messages = []
            self.tool_names = []

        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201
            self.messages.append(messages)
            self.tool_names.append(tool_names)
            return LLMResult(content="历史上下文摘要：\n- LLM 语义摘要：用户要保留架构决策。")

    llm = SummaryLLM()
    messages = [
        Message.user("很早以前的需求：" + "A" * 80),
        Message.agent("已记录架构决策：" + "B" * 80),
        Message.user("最新需求：继续"),
    ]

    compacted, snapshot = compact_messages_with_snapshot(messages, token_budget=30, llm=llm)

    assert snapshot is not None
    assert compacted[0].content == "历史上下文摘要：\n- LLM 语义摘要：用户要保留架构决策。"
    assert snapshot.summary == compacted[0].content
    assert llm.tool_names == [[]]


def test_compact_messages_falls_back_when_llm_summary_fails() -> None:
    class FailingLLM:
        def complete_with_tools(self, messages, tool_names):  # noqa: ANN001, ANN201, ARG002
            raise RuntimeError("llm unavailable")

    messages = [
        Message.user("旧需求：" + "A" * 80),
        Message.user("最新需求：继续"),
    ]

    compacted, snapshot = compact_messages_with_snapshot(messages, token_budget=20, llm=FailingLLM())

    assert snapshot is not None
    assert compacted[0].content.startswith("历史上下文摘要：")
    assert "旧需求" in compacted[0].content


def test_build_project_code_overview_includes_structure_and_notes(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# demo", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "manus_mini").mkdir(parents=True)
    (tmp_path / "src" / "manus_mini" / "runtime.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "design.md").write_text("design", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_runtime.py").write_text("def test_x(): pass", encoding="utf-8")

    overview = build_project_code_overview(tmp_path)

    assert "项目代码目录结构" in overview
    assert "README.md：项目说明、安装方式和使用入口" in overview
    assert "src/：核心实现代码" in overview
    assert "manus_mini/：主应用包，包含运行链路和工具逻辑" in overview
    assert "runtime.py：运行编排、外层循环和中断兜底" in overview
    assert "docs/：设计文档、问题记录和优化说明" in overview
    assert "tests/：自动化测试" in overview
    assert "建议优先查看" in overview


def test_should_include_project_code_overview_matches_code_related_requests() -> None:
    assert should_include_project_code_overview("请看下当前项目代码结构")
    assert should_include_project_code_overview("帮我分析一下这个工程")
    assert not should_include_project_code_overview("你好，今天怎么样")
