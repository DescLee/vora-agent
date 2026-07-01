from __future__ import annotations

import argparse
from pathlib import Path

from manus_mini.memory import MemoryManager
from manus_mini.session import SessionManager
from manus_mini.prompt_tui import PromptTui, PromptTuiOptions
from manus_mini.models import LoopLimits

PRINTABLE_KEY_ALIASES = {
    "period": "full_stop",
    "slash": "solidus",
    "backslash": "reverse_solidus",
    "minus": "hyphen_minus",
    "plus": "plus_sign",
    "underscore": "low_line",
}


def build_chat_input_bindings():
    from textual.binding import Binding
    from textual.widgets import Input

    return [
        binding for binding in Input.BINDINGS if binding.key != "enter"
    ] + [
        Binding(
            "ctrl+enter",
            "submit",
            "Send message",
            show=True,
        )
    ]


def key_to_chat_text(key: str, character: str | None) -> str | None:
    from textual.keys import key_to_character

    if character is not None and character.isprintable():
        return character

    text = key_to_character(PRINTABLE_KEY_ALIASES.get(key, key))
    if text is not None and text.isprintable():
        return text
    return None


def build_chat_input_class():
    from textual import events
    from textual.widgets import Input

    class ChatInput(Input):
        BINDINGS = build_chat_input_bindings()

        def check_consume_key(self, key: str, character: str | None) -> bool:
            return key_to_chat_text(key, character) is not None

        async def _on_key(self, event: events.Key) -> None:
            text = key_to_chat_text(event.key, event.character)
            if text is None:
                await super()._on_key(event)
                return

            event.stop()
            selection = self.selection
            if selection.is_empty:
                self.insert_text_at_cursor(text)
            else:
                self.replace(text, *selection)
            event.prevent_default()

    return ChatInput


def _build_app_class():
    from rich.markdown import Markdown
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, Input, Static

    ChatInput = build_chat_input_class()

    class ManusMiniApp(App):
        CSS = """
        Screen {
            background: #101418;
            color: #f3f0e8;
        }

        #root {
            height: 1fr;
        }

        #main {
            height: 1fr;
        }

        #messages {
            width: 3fr;
            border: solid #6ea8a1;
            padding: 1 2;
            background: #172026;
        }

        #artifact {
            width: 2fr;
            border: solid #c28f4d;
            padding: 1 2;
            background: #221b16;
        }

        #status {
            height: 3;
            padding: 0 2;
            background: #0f171b;
            color: #c7d4cf;
        }

        Input {
            dock: bottom;
            border: solid #6ea8a1;
            background: #121a20;
            color: #f3f0e8;
        }
        """

        def __init__(self, cwd: Path | None = None, max_steps: int | None = None, max_react: int | None = None, max_reflect: int | None = None, max_tool_timeout: int | None = None, dry_run: bool = False) -> None:
            super().__init__()
            limits = LoopLimits()
            if max_steps is not None:
                limits.max_engineering_steps = max_steps
            if max_react is not None:
                limits.max_react_iterations = max_react
            if max_reflect is not None:
                limits.max_reflection_rounds = max_reflect
            if max_tool_timeout is not None:
                limits.max_tool_timeout_seconds = max_tool_timeout
            resolved_cwd = cwd or Path.cwd()
            self.manager = SessionManager(
                resolved_cwd,
                default_limits=limits,
                dry_run=dry_run,
                memory_manager=MemoryManager(resolved_cwd / ".manus-mini" / "memory.db"),
            )

        def on_mount(self) -> None:
            self.call_after_refresh(self._focus_message_input)

        def _focus_message_input(self) -> None:
            self.query_one("#input", ChatInput).focus()

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical(id="root"):
                with Horizontal(id="main"):
                    yield Static("等待输入...", id="messages")
                    yield Static("当前产物会显示在这里", id="artifact")
                limits = self.manager.runtime.default_limits
                yield Static(
                    f"step 0/{limits.max_engineering_steps} | react 0/{limits.max_react_iterations} | reflect 0/{limits.max_reflection_rounds} | idle",
                    id="status",
                )
            yield ChatInput(placeholder="继续输入你的要求...", id="input")
            yield Footer()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            content = event.value.strip()
            event.input.value = ""
            self.call_after_refresh(self._focus_message_input)
            if not content:
                return

            session = self.manager.handle_user_message(content)
            self._render_session(session)

        def _render_session(self, session) -> None:
            message_text = "\n\n".join(
                f"**{message.role}**: {message.content}" for message in session.messages
            )
            self.query_one("#messages", Static).update(Markdown(message_text))

            if session.active_task is None:
                return

            task = session.active_task
            self.query_one("#artifact", Static).update(task.result or "暂无产物")
            self.query_one("#status", Static).update(
                " | ".join(
                    [
                        f"step {task.step_count}/{task.limits.max_engineering_steps}",
                        f"react 0/{task.limits.max_react_iterations}",
                        f"reflect 0/{task.limits.max_reflection_rounds}",
                        task.status,
                    ]
                )
            )

    return ManusMiniApp


ManusMiniApp = None


def main_textual() -> None:
    parser = argparse.ArgumentParser(prog="manus-mini-textual")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--max-steps", type=int, default=99)
    parser.add_argument("--max-react", type=int, default=99)
    parser.add_argument("--max-reflect", type=int, default=99)
    parser.add_argument("--max-tool-timeout", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    global ManusMiniApp
    if ManusMiniApp is None:
        ManusMiniApp = _build_app_class()
    ManusMiniApp(
        cwd=args.cwd,
        max_steps=args.max_steps,
        max_react=args.max_react,
        max_reflect=args.max_reflect,
        max_tool_timeout=args.max_tool_timeout,
        dry_run=args.dry_run,
    ).run()


def main() -> None:
    parser = argparse.ArgumentParser(prog="manus-mini")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--max-steps", type=int, default=99)
    parser.add_argument("--max-react", type=int, default=99)
    parser.add_argument("--max-reflect", type=int, default=99)
    parser.add_argument("--max-tool-retries", type=int, default=99)
    parser.add_argument("--max-tool-timeout", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    limits = LoopLimits(
        max_engineering_steps=args.max_steps,
        max_react_iterations=args.max_react,
        max_reflection_rounds=args.max_reflect,
        max_tool_retries=args.max_tool_retries,
        max_tool_timeout_seconds=args.max_tool_timeout,
    )
    PromptTui(PromptTuiOptions(cwd=args.cwd, limits=limits, dry_run=args.dry_run)).run()
