import asyncio
from pathlib import Path

from manus_mini.app import _build_app_class, build_chat_input_bindings, key_to_chat_text, main
from manus_mini.models import Message, SessionState
from manus_mini.session_store import SessionStore


def test_chat_input_bindings_keep_enter_free_for_ime() -> None:
    bindings = build_chat_input_bindings()

    assert all(binding.key != "enter" for binding in bindings)
    assert any(binding.key == "ctrl+enter" and binding.action == "submit" for binding in bindings)


def test_key_to_chat_text_accepts_space_punctuation_and_chinese() -> None:
    assert key_to_chat_text("space", None) == " "
    assert key_to_chat_text("comma", None) == ","
    assert key_to_chat_text("full_stop", None) == "."
    assert key_to_chat_text("question_mark", None) == "?"
    assert key_to_chat_text("你", None) == "你"


def test_chat_input_accepts_sentence_characters() -> None:
    async def run() -> None:
        app_class = _build_app_class()
        async with app_class().run_test() as pilot:
            await pilot.press("h")
            await pilot.press("space")
            await pilot.press("comma")
            await pilot.press("你")

            assert pilot.app.query_one("#input").value == "h ,你"

    asyncio.run(run())


def test_textual_app_can_be_parameterized(tmp_path: Path) -> None:
    app_class = _build_app_class()
    app = app_class(cwd=tmp_path, max_steps=3, max_react=4, max_reflect=2, max_tool_timeout=11, dry_run=True)

    assert app.manager.current.cwd == tmp_path
    assert app.manager.runtime.default_limits.max_engineering_steps == 3
    assert app.manager.runtime.default_limits.max_react_iterations == 4
    assert app.manager.runtime.default_limits.max_reflection_rounds == 2
    assert app.manager.runtime.default_limits.max_tool_timeout_seconds == 11
    assert app.manager.runtime.dry_run is True


def test_textual_app_uses_wider_default_limits() -> None:
    app_class = _build_app_class()
    app = app_class()

    assert app.manager.runtime.default_limits.max_engineering_steps == 99
    assert app.manager.runtime.default_limits.max_react_iterations == 99
    assert app.manager.runtime.default_limits.max_reflection_rounds == 99
    assert app.manager.runtime.default_limits.max_tool_retries == 99


def test_main_list_prints_workspace_sessions(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("继续分析这个项目"))
    SessionStore(tmp_path).save(session)

    exit_code = main(["list", "--cwd", str(tmp_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert session.session_id in output
    assert "继续分析这个项目" in output


def test_main_resume_loads_existing_session(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    session = SessionState.create(cwd=tmp_path)
    session.messages.append(Message.user("上一轮上下文"))
    SessionStore(tmp_path).save(session)
    captured = {}

    class FakeTui:
        def __init__(self, options=None, cwd=None, initial_session=None):  # noqa: ANN001
            captured["session"] = initial_session
            captured["cwd"] = cwd

        def run(self) -> None:
            captured["ran"] = True

    monkeypatch.setattr("manus_mini.app.PromptTui", FakeTui)

    exit_code = main(["resume", session.session_id, "--cwd", str(tmp_path)])

    assert exit_code == 0
    assert captured["ran"] is True
    assert captured["session"].messages[0].content == "上一轮上下文"


def test_main_resume_missing_session_prints_friendly_error(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    exit_code = main(["resume", "session-missing", "--cwd", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "找不到对话" in captured.err
    assert "session-missing" in captured.err
    assert "manus-mini list" in captured.err
    assert "Traceback" not in captured.err
