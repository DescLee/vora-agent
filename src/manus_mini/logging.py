from __future__ import annotations

import json
import os
import hashlib
import tempfile
from shutil import copy2
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


MAX_LOG_MESSAGE_CONTENT_CHARS = 1200
MAX_REFLECTION_OBSERVATIONS = 12


def default_manus_home() -> Path:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return Path(tempfile.gettempdir()) / "manus-mini" / ".manus-mini"
    return Path.home() / ".manus-mini"


def default_runs_dir() -> Path:
    return default_manus_home() / "runs"


def project_storage_dir(cwd: Path) -> Path:
    resolved = cwd.expanduser().resolve(strict=False)
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in resolved.name)
    return default_manus_home() / "projects" / f"{safe_name}-{digest}"


def project_runs_dir(cwd: Path) -> Path:
    return project_storage_dir(cwd) / "runs"


def project_sessions_dir(cwd: Path) -> Path:
    return project_storage_dir(cwd) / "sessions"


def project_memory_path(cwd: Path) -> Path:
    return project_storage_dir(cwd) / "memory.db"


def migrate_legacy_project_storage(cwd: Path) -> list[Path]:
    """Copy old project-local .manus-mini data into the project-isolated user store."""
    legacy_root = cwd / ".manus-mini"
    if not legacy_root.exists():
        return []

    copied: list[Path] = []
    legacy_sessions_dir = legacy_root / "sessions"
    if legacy_sessions_dir.exists():
        target_sessions_dir = project_sessions_dir(cwd)
        for source in legacy_sessions_dir.glob("*.json"):
            if not source.is_file():
                continue
            target = target_sessions_dir / source.name
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            copy2(source, target)
            copied.append(target)

    legacy_memory = legacy_root / "memory.db"
    target_memory = project_memory_path(cwd)
    if legacy_memory.is_file() and not target_memory.exists():
        target_memory.parent.mkdir(parents=True, exist_ok=True)
        copy2(legacy_memory, target_memory)
        copied.append(target_memory)

    return copied


class EventLogger:
    def __init__(self, root: Path | None = None, enabled: bool | None = None) -> None:
        self.root = root or default_runs_dir()
        self.filename = f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}-event.jsonl"
        if enabled is None:
            enabled = os.environ.get("MANUS_DISABLE_LOGGING") != "1" and not os.environ.get("PYTEST_CURRENT_TEST")
        self.enabled = enabled

    def record(self, session_id: str, run_id: str, event: dict[str, Any]) -> Path:
        run_dir = self.root / f"{session_id}-{run_id}"
        path = run_dir / self.filename
        if not self.enabled:
            return path
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "run_id": run_id,
            **compact_event(event),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path


def compact_event(event: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(event)
    if compacted.get("api_request_payload") == compacted.get("request"):
        compacted.pop("api_request_payload", None)
    if compacted.get("api_response_raw") == compacted.get("response"):
        compacted.pop("api_response_raw", None)
    if compacted.get("type") == "llm_response":
        compacted.pop("request", None)
        compacted.pop("api_request_payload", None)
    request = compacted.get("request")
    if isinstance(request, dict):
        compacted["request"] = compact_llm_payload(request)
    response = compacted.get("response")
    if isinstance(response, dict):
        compacted["response"] = compact_llm_payload(response)
    reflection_context = compacted.get("reflection_context")
    if isinstance(reflection_context, dict):
        compacted["reflection_context"] = compact_reflection_context(reflection_context)
    return compacted


def compact_llm_payload(payload: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(payload)
    messages = compacted.get("messages")
    if isinstance(messages, list):
        compacted["messages"] = [
            compact_llm_message(message)
            for message in messages
            if isinstance(message, dict)
        ]
    choices = compacted.get("choices")
    if isinstance(choices, list):
        compacted["choices"] = [
            compact_llm_choice(choice)
            for choice in choices
            if isinstance(choice, dict)
        ]
    return compacted


def compact_llm_choice(choice: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(choice)
    message = compacted.get("message")
    if isinstance(message, dict):
        compacted["message"] = compact_llm_message(message)
    return compacted


def compact_llm_message(message: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(message)
    for key in ("content", "reasoning_content"):
        content = compacted.get(key)
        if not isinstance(content, str) or len(content) <= MAX_LOG_MESSAGE_CONTENT_CHARS:
            continue
        compacted[f"{key}_preview"] = content[:MAX_LOG_MESSAGE_CONTENT_CHARS]
        compacted["content_omitted"] = True
        compacted.pop(key, None)
    return compacted


def compact_reflection_context(context: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(context)
    observations = compacted.get("observations")
    if isinstance(observations, list):
        omitted = max(0, len(observations) - MAX_REFLECTION_OBSERVATIONS)
        if omitted:
            compacted["observations_omitted"] = omitted
        visible_observations = observations[-MAX_REFLECTION_OBSERVATIONS:]
        compacted["observations"] = [
            compact_observation(observation)
            for observation in visible_observations
            if isinstance(observation, dict)
        ]
    return compacted


def compact_observation(observation: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(observation)
    content = compacted.pop("content", "")
    if isinstance(content, str) and content:
        compacted["content_preview"] = content[:500]
        compacted["content_omitted"] = len(content) > 500
    return compacted
