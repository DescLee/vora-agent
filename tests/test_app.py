import asyncio

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
