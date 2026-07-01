from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from manus_mini.models import SessionState


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    updated_at: datetime
    message_count: int
    last_user_message: str
    path: Path


class SessionStore:
    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.sessions_dir = cwd / ".manus-mini" / "sessions"

    def save(self, session: SessionState) -> Path:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self._path_for(session.session_id)
        path.write_text(
            session.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return path

    def load(self, session_id: str) -> SessionState:
        path = self._path_for(session_id)
        if not path.exists():
            raise FileNotFoundError(f"session not found: {session_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        session = SessionState.model_validate(data)
        session.cwd = self.cwd
        if session.active_task is not None:
            session.active_task.cwd = self.cwd
        return session

    def list_sessions(self) -> list[SessionSummary]:
        if not self.sessions_dir.exists():
            return []
        summaries = [self._summary(path) for path in self.sessions_dir.glob("*.json")]
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def _summary(self, path: Path) -> SessionSummary:
        data = json.loads(path.read_text(encoding="utf-8"))
        session = SessionState.model_validate(data)
        last_user_message = ""
        for message in reversed(session.messages):
            if message.role == "user":
                last_user_message = message.content
                break
        updated_at = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        return SessionSummary(
            session_id=session.session_id,
            updated_at=updated_at,
            message_count=len(session.messages),
            last_user_message=last_user_message,
            path=path,
        )

    def _path_for(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"
