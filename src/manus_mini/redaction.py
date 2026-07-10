from __future__ import annotations

import re
from typing import Any


SECRET_VALUE_PATTERNS = [
    re.compile(r"(?i)(?P<prefix>\bAuthorization\s*:\s*Bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(
        r"(?i)(?P<prefix>[?&](?:access[_-]?token|refresh[_-]?token|api[_-]?key|token|password|secret)=)"
        r"[^&#\s`'\"]+"
    ),
    re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b"),
    re.compile(
        r"(?i)(?P<prefix>\b[A-Z0-9_.-]*(?:API[_-]?KEY|TOKEN|PASSWORD|SECRET)[A-Z0-9_.-]*\b\s*[:=]\s*['\"]?)"
        r"[^&#'\"\s`]+"
    ),
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b\s*[:=]\s*['\"]?[^'\"\s`]+"),
    re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b\s+(?:is|是|为)\s*['\"]?[^'\"\s`]+"),
]


def redact_sensitive_text(content: str) -> str:
    redacted = content
    for pattern in SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(lambda match: _redact_match(match), redacted)
    return redacted


def redact_sensitive_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        return {
            key: _redact_sensitive_key_value(item) if _is_sensitive_key(str(key)) else redact_sensitive_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    return value


def contains_sensitive_text(content: str) -> bool:
    return redact_sensitive_text(content) != content


def _redact_match(match: re.Match[str]) -> str:
    prefix = match.groupdict().get("prefix")
    if prefix is not None:
        return f"{prefix}[REDACTED]"
    if match.lastindex:
        key = match.group(1)
        return f"{key}=[REDACTED]"
    return "[REDACTED]"


def _redact_sensitive_key_value(value: Any) -> Any:
    if isinstance(value, str):
        redacted = redact_sensitive_text(value)
        return redacted if redacted != value else "[REDACTED]"
    return "[REDACTED]"


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    sensitive_keys = {
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "token",
        "password",
        "secret",
    }
    return (
        normalized in sensitive_keys
        or normalized.endswith(("_api_key", "_token", "_password", "_secret"))
        or "secret_access_key" in normalized
    )
