from __future__ import annotations

import json
import os
import hashlib
import re
import tempfile
from shutil import copy2
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vora.redaction import redact_sensitive_text, redact_sensitive_value


MAX_LOG_MESSAGE_CONTENT_CHARS = 1200
MAX_REFLECTION_OBSERVATIONS = 12
LOG_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def default_vora_home() -> Path:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return Path(tempfile.gettempdir()) / "vora" / ".vora"
    return Path.home() / ".vora"


def default_logs_dir() -> Path:
    return default_vora_home() / "logs"


def default_runs_dir() -> Path:
    return default_logs_dir()


def project_storage_dir(cwd: Path) -> Path:
    resolved = cwd.expanduser().resolve(strict=False)
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in resolved.name)
    preferred = default_vora_home() / "projects" / f"{safe_name}-{digest}"
    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
        preferred.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        fallback = resolved / ".vora"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    return preferred


def project_logs_dir(cwd: Path) -> Path:
    return project_storage_dir(cwd) / "logs"


def project_runs_dir(cwd: Path) -> Path:
    return project_logs_dir(cwd)


def project_outputs_dir(cwd: Path) -> Path:
    return project_storage_dir(cwd) / "outputs"


def project_sessions_dir(cwd: Path) -> Path:
    return project_storage_dir(cwd) / "sessions"


def project_memory_path(cwd: Path) -> Path:
    return project_storage_dir(cwd) / "memory.db"


def migrate_legacy_project_storage(cwd: Path) -> list[Path]:
    """Copy old project-local storage into the project-isolated user store."""
    copied: list[Path] = []
    for legacy_root in [cwd / ".vora", cwd / ".manus-mini"]:
        if not legacy_root.exists():
            continue
        legacy_sessions_dir = legacy_root / "sessions"
        if legacy_sessions_dir.exists():
            target_sessions_dir = project_sessions_dir(cwd)
            for source in legacy_sessions_dir.glob("*.json"):
                if source.is_symlink() or not source.is_file():
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


def _ensure_real_log_dir(path: Path) -> None:
    if path.is_symlink():
        raise OSError(f"log session directory is a symlink: {path}")


class EventLogger:
    def __init__(self, root: Path | None = None, enabled: bool | None = None) -> None:
        self.root = root or default_logs_dir()
        self.summary_filename = "summary.jsonl"
        self.pipeline_filename = "pipeline.jsonl"
        self.node_filename = "node.jsonl"
        self._run_node_counts: dict[tuple[str, str], int] = {}
        self._last_node_ids: dict[tuple[str, str], str] = {}
        if enabled is None:
            enabled = os.environ.get("VORA_DISABLE_LOGGING") != "1" and not os.environ.get("PYTEST_CURRENT_TEST")
        self.enabled = enabled

    def record(self, session_id: str, run_id: str, event: dict[str, Any]) -> Path:
        _validate_log_session_id(session_id)
        session_dir = self.root / session_id
        node_path = session_dir / self.node_filename
        node_id = self._next_node_id(session_id, run_id, event)
        upstream_node_ids = self._upstream_node_ids(session_id, run_id, event)
        self._last_node_ids[(session_id, run_id)] = node_id
        if not self.enabled:
            return node_path
        _ensure_real_log_dir(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).isoformat()
        compacted_event = redact_sensitive_value(compact_event(event))
        payload = {
            "ts": ts,
            "session_id": session_id,
            "run_id": run_id,
            "node_id": node_id,
            "upstream_node_ids": upstream_node_ids,
            **compacted_event,
        }
        pipeline_payload = build_pipeline_event(ts, session_id, run_id, node_id, upstream_node_ids, compacted_event)
        with (session_dir / self.pipeline_filename).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(pipeline_payload, ensure_ascii=False) + "\n")
        with node_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return node_path

    def record_summary(
        self,
        session_id: str,
        run_id: str,
        user_input: str,
        result: str,
        status: str,
    ) -> Path:
        _validate_log_session_id(session_id)
        session_dir = self.root / session_id
        path = session_dir / self.summary_filename
        if not self.enabled:
            return path
        _ensure_real_log_dir(session_dir)
        session_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "session_id": session_id,
            "run_id": run_id,
            "final_node_id": self._last_node_ids.get((session_id, run_id)),
            "user_input": redact_sensitive_text(user_input),
            "status": status,
            "result": redact_sensitive_text(result),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return path

    def _next_node_id(self, session_id: str, run_id: str, event: dict[str, Any]) -> str:
        explicit = event.get("node_id")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        key = (session_id, run_id)
        count = self._run_node_counts.get(key, 0) + 1
        self._run_node_counts[key] = count
        return f"{run_id}:node-{count:04d}"

    def _upstream_node_ids(self, session_id: str, run_id: str, event: dict[str, Any]) -> list[str]:
        explicit = event.get("upstream_node_ids")
        if isinstance(explicit, list):
            return [str(item) for item in explicit if str(item).strip()]
        previous = self._last_node_ids.get((session_id, run_id))
        return [previous] if previous else []


def build_pipeline_event(
    ts: str,
    session_id: str,
    run_id: str,
    node_id: str,
    upstream_node_ids: list[str],
    event: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ts": ts,
        "session_id": session_id,
        "run_id": run_id,
        "node_id": node_id,
        "node": pipeline_node_name(event),
        "status": pipeline_status(event),
        "message_id": event.get("message_id") or event.get("tool_call_id") or event.get("id"),
        "upstream_node_ids": upstream_node_ids,
    }


def _validate_log_session_id(session_id: str) -> None:
    if not LOG_SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"invalid session_id: {session_id}")
    if "/" in session_id or "\\" in session_id:
        raise ValueError(f"invalid session_id: {session_id}")


def pipeline_node_name(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "event")
    stage = event.get("stage")
    if stage:
        return f"{stage}.{event_type}"
    return event_type


def pipeline_status(event: dict[str, Any]) -> str:
    event_type = event.get("type")
    if event_type == "result":
        return str(event.get("status") or "done")
    if event_type in {"error"}:
        return "failed"
    if event_type == "interrupt":
        return "cancelled"
    if event.get("ok") is True:
        return "success"
    if event.get("ok") is False:
        return "failed"
    if event_type in {"llm_request", "engineering_step", "user_input"}:
        return "running"
    return "recorded"


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
