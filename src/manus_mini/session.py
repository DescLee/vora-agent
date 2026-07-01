from __future__ import annotations

from pathlib import Path

from manus_mini.models import SessionState
from manus_mini.runtime import AgentRuntime


class SessionManager:
    def __init__(self, cwd: Path, runtime: AgentRuntime | None = None) -> None:
        self.runtime = runtime or AgentRuntime()
        self.current = SessionState.create(cwd=cwd)

    def handle_user_message(self, content: str, append_user_message: bool = True) -> SessionState:
        self.current = self.runtime.on_user_message(content, self.current, append_user_message=append_user_message)
        return self.current
