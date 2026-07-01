import asyncio
from pathlib import Path

from manus_mini.app import _build_app_class, build_chat_input_bindings, key_to_chat_text


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
