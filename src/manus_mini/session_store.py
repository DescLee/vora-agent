from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from manus_mini.context import complete_interrupted_tool_messages
from manus_mini.logging import migrate_legacy_project_storage, project_logs_dir, project_sessions_dir
from manus_mini.models import SessionState


CURRENT_SESSION_SCHEMA_VERSION = 1
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
KNOWN_ERROR_CODES = {
    "FILE_NOT_FOUND",
    "PATH_OUT_OF_WORKSPACE",
    "INVALID_TOOL_PARAMS",
    "USER_CANCELLED",
    "MAX_STEPS_REACHED",
    "MAX_REACT_ITERATIONS_REACHED",
    "MAX_REFLECTION_ROUNDS_REACHED",
    "RUNTIME_TIMEOUT",
    "TOKEN_BUDGET_EXCEEDED",
    "TOOL_TIMEOUT",
    "TOOL_RETRY_EXHAUSTED",
    "INVALID_LLM_OUTPUT",
    "LLM_ERROR",
    "UNKNOWN_ERROR",
}


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
        self.sessions_dir = project_sessions_dir(cwd)
        migrate_legacy_project_storage(cwd)

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
        session = SessionState.model_validate(migrate_session_data(data))
        session.cwd = self.cwd
        if session.active_task is not None:
            session.active_task.cwd = self.cwd
        complete_interrupted_tool_messages(session.messages)
        return session

    def list_sessions(self) -> list[SessionSummary]:
        if not self.sessions_dir.exists():
            return []
        summaries = []
        for path in self.sessions_dir.glob("*.json"):
            try:
                summaries.append(self._summary(path))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
        return sorted(summaries, key=lambda item: item.updated_at, reverse=True)

    def delete(self, session_id: str) -> bool:
        """Delete a saved session by its session_id.

        Returns True if the session was found and deleted, False otherwise.
        """
        path = self._path_for(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def clear_all(self) -> int:
        """Delete all saved sessions.

        Returns the number of sessions that were deleted.
        """
        if not self.sessions_dir.exists():
            return 0
        count = 0
        for path in self.sessions_dir.glob("*.json"):
            path.unlink()
            count += 1
        return count

    # ──────────────────────────────────────────────
    #  Logs 同步清理
    # ──────────────────────────────────────────────

    def _logs_dir(self) -> Path:
        """返回 session 级日志目录路径。"""
        return project_logs_dir(self.cwd)

    def delete_logs_for_session(self, session_id: str) -> int:
        """删除 logs 目录下指定 session 的日志目录。

        Args:
            session_id: 会话 ID，例如 "session-abc123"

        Returns:
            删除的目录数量
        """
        validate_session_id(session_id)
        log_dir = self._logs_dir() / session_id
        if not log_dir.exists():
            return 0
        shutil.rmtree(log_dir, ignore_errors=True)
        return 1

    def delete_runs_for_session(self, session_id: str) -> int:
        return self.delete_logs_for_session(session_id)

    def clear_all_logs(self) -> int:
        """清空 logs 目录下所有 session 日志目录。

        Returns:
            删除的目录数量
        """
        logs_dir = self._logs_dir()
        if not logs_dir.exists():
            return 0
        count = 0
        for child in logs_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                count += 1
        return count

    def clear_all_runs(self) -> int:
        return self.clear_all_logs()

    def _summary(self, path: Path) -> SessionSummary:
        data = json.loads(path.read_text(encoding="utf-8"))
        session = SessionState.model_validate(migrate_session_data(data))
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
        validate_session_id(session_id)
        return self.sessions_dir / f"{session_id}.json"


def validate_session_id(session_id: str) -> None:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"invalid session_id: {session_id}")
    if "/" in session_id or "\\" in session_id:
        raise ValueError(f"invalid session_id: {session_id}")


def migrate_session_data(data: dict) -> dict:
    migrated = dict(data)
    migrated["schema_version"] = CURRENT_SESSION_SCHEMA_VERSION
    active_task = migrated.get("active_task")
    if isinstance(active_task, dict):
        migrated["active_task"] = _migrate_task_data(active_task)
    return migrated


def _migrate_task_data(data: dict) -> dict:
    migrated = dict(data)
    errors = migrated.get("errors")
    if isinstance(errors, list):
        migrated["errors"] = [_migrate_error_data(error) for error in errors]
    return migrated


def _migrate_error_data(data) -> dict:
    if not isinstance(data, dict):
        return {"code": "UNKNOWN_ERROR", "message": str(data), "retryable": False}
    migrated = dict(data)
    code = str(migrated.get("code") or "UNKNOWN_ERROR")
    if code in KNOWN_ERROR_CODES:
        return migrated
    metadata = dict(migrated.get("metadata") or {})
    metadata["legacy_code"] = code
    migrated["code"] = "UNKNOWN_ERROR"
    migrated["metadata"] = metadata
    return migrated
