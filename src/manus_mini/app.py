from __future__ import annotations

from pathlib import Path

from manus_mini.session import SessionManager

def _build_app_class():
    from rich.markdown import Markdown
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, Input, Static

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

        def __init__(self, cwd: Path | None = None) -> None:
            super().__init__()
            self.manager = SessionManager(cwd or Path.cwd())

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Vertical(id="root"):
                with Horizontal(id="main"):
                    yield Static("等待输入...", id="messages")
                    yield Static("当前产物会显示在这里", id="artifact")
                yield Static("step 0/8 | react 0/5 | reflect 0/3 | idle", id="status")
            yield Input(placeholder="继续输入你的要求...", id="input")
            yield Footer()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            content = event.value.strip()
            event.input.value = ""
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


def main() -> None:
    global ManusMiniApp
    if ManusMiniApp is None:
        ManusMiniApp = _build_app_class()
    ManusMiniApp().run()
